package main

import (
	"bytes"
	"crypto/hmac"
	"crypto/sha1"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"log"
	"net/http"
	"os"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/gorilla/websocket"
	"github.com/pion/webrtc/v4"
)

type turnServer struct {
	URL            string `json:"url"`
	Username       string `json:"username"`
	Credential     string `json:"credential"`
	CredentialType string `json:"credentialType"`
	IsTLS          bool   `json:"isTls"`
}

type tokenPayload struct {
	MatchID        string       `json:"matchId"`
	PlayerID       string       `json:"playerId"`
	DisplayName    string       `json:"displayName"`
	Role           string       `json:"role"`
	TeamID         int          `json:"teamId"`
	SessionID      string       `json:"sessionId"`
	IssuedAtEpoch  int64        `json:"issuedAtEpoch"`
	ExpiresAtEpoch int64        `json:"expiresAtEpoch"`
	GatewayHTTPURL string       `json:"gatewayHttpUrl"`
	GatewayWSURL   string       `json:"gatewayWebSocketUrl"`
	Mode           string       `json:"mode"`
	CrossRoleMode  string       `json:"crossRoleMode"`
	ContourHz      int          `json:"contourHz"`
	FakeVoiceBank  string       `json:"fakeVoiceBankKey"`
	TurnServers    []turnServer `json:"turnServers"`
}

type contourPacket struct {
	PlayerID      string  `json:"playerId"`
	SequenceID    int     `json:"sequenceId"`
	IsActive      bool    `json:"isActive"`
	Envelope      float64 `json:"envelope"`
	PitchNorm     float64 `json:"pitchNorm"`
	Stress        float64 `json:"stress"`
	Burst         float64 `json:"burst"`
	DurationMS    float64 `json:"durationMs"`
	CreatedAtEpoch int64  `json:"createdAtEpoch"`
	BankHint      string  `json:"bankHint"`
}

type signalMessage struct {
	Type               string         `json:"type"`
	SourcePeerID       string         `json:"sourcePeerId,omitempty"`
	TargetPeerID       string         `json:"targetPeerId,omitempty"`
	Detail             string         `json:"detail,omitempty"`
	SDP                string         `json:"sdp,omitempty"`
	Candidate          string         `json:"candidate,omitempty"`
	SDPMid             string         `json:"sdpMid,omitempty"`
	SDPMLineIndex      int            `json:"sdpMLineIndex,omitempty"`
	IsSpeaking         bool           `json:"isSpeaking,omitempty"`
	HasInput           bool           `json:"hasInput,omitempty"`
	DeviceLost         bool           `json:"deviceLost,omitempty"`
	PacketLossPercent  float64        `json:"packetLossPercent,omitempty"`
	JitterMS           float64        `json:"jitterMs,omitempty"`
	Packet             *contourPacket `json:"packet,omitempty"`
	RoomMode           string         `json:"roomMode,omitempty"`
	CrossRoleMode      string         `json:"crossRoleMode,omitempty"`
	Stats              map[string]any `json:"stats,omitempty"`
	Peer               map[string]any `json:"peer,omitempty"`
}

type forwardedSubscriber struct {
	targetPeerID string
	track        *webrtc.TrackLocalStaticRTP
	sender       *webrtc.RTPSender
}

type forwardedTrack struct {
	sourcePeerID string
	sourceRole   string
	codec        webrtc.RTPCodecCapability
	subscribers  map[string]*forwardedSubscriber
	mu           sync.RWMutex
}

type peerState struct {
	peerID            string
	displayName       string
	role              string
	teamID            int
	sessionID         string
	matchID           string
	mode              string
	crossRoleMode     string
	ws                *websocket.Conn
	pc                *webrtc.PeerConnection
	contourDC         *webrtc.DataChannel
	connectedAt       time.Time
	packetLossPercent float64
	jitterMS          float64
	isSpeaking        bool
	hasInput          bool
	deviceLost        bool
	mu                sync.RWMutex
}

type roomState struct {
	matchID                  string
	mode                     string
	crossRoleMode            string
	fakeVoiceBankKey         string
	contourHz                int
	peers                    map[string]*peerState
	forwards                 map[string]*forwardedTrack
	audioFramesRelayed       int64
	audioBytesRelayed        int64
	contourPacketsRelayed    int64
	droppedPeers             int64
	updatedAt                time.Time
	lastStatsPublishedAt     time.Time
	mu                       sync.RWMutex
}

type gatewayState struct {
	rooms map[string]*roomState
	mu    sync.RWMutex
}

var (
	projectName             = envString("UNDERFOOT_PROJECT_NAME", "underfoot")
	worldServiceURL         = strings.TrimRight(envString("UNDERFOOT_WORLD_SERVICE_URL", "http://underfoot-world:8460"), "/")
	voiceJoinSecret         = envString("UNDERFOOT_VOICE_JOIN_SECRET", "")
	voiceGatewayPort        = envString("UNDERFOOT_VOICE_GATEWAY_PORT", "8470")
	voiceTurnHost           = envString("UNDERFOOT_VOICE_TURN_HOST", "")
	voiceTurnPort           = envInt("UNDERFOOT_VOICE_TURN_PORT", 3478)
	voiceTurnTLSPort        = envInt("UNDERFOOT_VOICE_TURN_TLS_PORT", 5349)
	voiceTurnSharedSecret   = envString("UNDERFOOT_VOICE_TURN_SHARED_SECRET", "")
	publishStatsMinInterval = time.Second
	upgrader                = websocket.Upgrader{CheckOrigin: func(r *http.Request) bool { return true }}
	state                   = &gatewayState{rooms: map[string]*roomState{}}
	httpClient              = &http.Client{Timeout: 4 * time.Second}
)

func envString(key string, fallback string) string {
	value := strings.TrimSpace(os.Getenv(key))
	if value == "" {
		return fallback
	}
	return value
}

func envInt(key string, fallback int) int {
	raw := strings.TrimSpace(os.Getenv(key))
	if raw == "" {
		return fallback
	}
	value, err := strconv.Atoi(raw)
	if err != nil {
		return fallback
	}
	return value
}

func nowEpoch() int64 {
	return time.Now().Unix()
}

func decodeVoiceToken(rawToken string) (*tokenPayload, error) {
	if len(voiceJoinSecret) < 32 {
		return nil, fmt.Errorf("voice_join_secret_not_configured")
	}
	clean := strings.TrimSpace(rawToken)
	parts := strings.SplitN(clean, ".", 2)
	if len(parts) != 2 {
		return nil, fmt.Errorf("malformed voice token")
	}

	encoded := parts[0]
	signature := parts[1]
	padding := len(encoded) % 4
	if padding != 0 {
		encoded += strings.Repeat("=", 4-padding)
	}
	body, err := base64.URLEncoding.DecodeString(encoded)
	if err != nil {
		return nil, fmt.Errorf("invalid token body: %w", err)
	}
	expected := hmac.New(sha256.New, []byte(voiceJoinSecret))
	expected.Write(body)
	if !hmac.Equal([]byte(fmt.Sprintf("%x", expected.Sum(nil))), []byte(signature)) {
		return nil, fmt.Errorf("invalid voice token signature")
	}

	payload := &tokenPayload{}
	if err := json.Unmarshal(body, payload); err != nil {
		return nil, fmt.Errorf("invalid token payload: %w", err)
	}
	if payload.ExpiresAtEpoch < nowEpoch() {
		return nil, fmt.Errorf("voice token expired")
	}
	if payload.Mode == "" {
		payload.Mode = "match_strict_illusion"
	}
	if payload.CrossRoleMode == "" {
		payload.CrossRoleMode = "contour_only"
	}
	if payload.ContourHz <= 0 {
		payload.ContourHz = 20
	}
	return payload, nil
}

func buildTurnCredential(username string) string {
	if len(voiceTurnSharedSecret) < 32 || voiceTurnHost == "" {
		return ""
	}
	mac := hmac.New(sha1.New, []byte(voiceTurnSharedSecret))
	mac.Write([]byte(username))
	return base64.StdEncoding.EncodeToString(mac.Sum(nil))
}

func ensureRoom(payload *tokenPayload) *roomState {
	state.mu.Lock()
	defer state.mu.Unlock()
	room, ok := state.rooms[payload.MatchID]
	if !ok {
		room = &roomState{
			matchID:          payload.MatchID,
			mode:             payload.Mode,
			crossRoleMode:    payload.CrossRoleMode,
			fakeVoiceBankKey: payload.FakeVoiceBank,
			contourHz:        payload.ContourHz,
			peers:            map[string]*peerState{},
			forwards:         map[string]*forwardedTrack{},
			updatedAt:        time.Now(),
		}
		state.rooms[payload.MatchID] = room
	}
	if room.mode == "" {
		room.mode = payload.Mode
	}
	if room.crossRoleMode == "" {
		room.crossRoleMode = payload.CrossRoleMode
	}
	if room.fakeVoiceBankKey == "" {
		room.fakeVoiceBankKey = payload.FakeVoiceBank
	}
	if room.contourHz <= 0 {
		room.contourHz = payload.ContourHz
	}
	return room
}

func rawAudioAllowed(room *roomState, source *peerState, listener *peerState) bool {
	if source == nil || listener == nil || source.peerID == listener.peerID {
		return false
	}
	if source.role == listener.role {
		return true
	}
	return strings.EqualFold(room.crossRoleMode, "intelligible_filtered")
}

func contourAllowed(room *roomState, source *peerState, listener *peerState) bool {
	if source == nil || listener == nil || source.peerID == listener.peerID {
		return false
	}
	if source.role == listener.role {
		return false
	}
	return !strings.EqualFold(room.crossRoleMode, "intelligible_filtered")
}

func newPeerConnection() (*webrtc.PeerConnection, error) {
	mediaEngine := &webrtc.MediaEngine{}
	if err := mediaEngine.RegisterDefaultCodecs(); err != nil {
		return nil, err
	}
	api := webrtc.NewAPI(webrtc.WithMediaEngine(mediaEngine))
	return api.NewPeerConnection(webrtc.Configuration{})
}

func (room *roomState) overviewStats() map[string]any {
	room.mu.RLock()
	defer room.mu.RUnlock()
	return room.statsLocked()
}

func (room *roomState) statsLocked() map[string]any {
	activePeers := len(room.peers)
	connectedPeers := 0
	speakingPeers := 0
	packetLossTotal := 0.0
	jitterTotal := 0.0
	rawSubscriptions := 0
	contourOnlySubscriptions := 0

	for _, peer := range room.peers {
		connectedPeers++
		peer.mu.RLock()
		if peer.isSpeaking {
			speakingPeers++
		}
		packetLossTotal += peer.packetLossPercent
		jitterTotal += peer.jitterMS
		peer.mu.RUnlock()
	}
	for _, forward := range room.forwards {
		forward.mu.RLock()
		rawSubscriptions += len(forward.subscribers)
		forward.mu.RUnlock()
	}
	for _, source := range room.peers {
		for _, listener := range room.peers {
			if contourAllowed(room, source, listener) {
				contourOnlySubscriptions++
			}
		}
	}

	avgPacketLoss := 0.0
	avgJitter := 0.0
	if connectedPeers > 0 {
		avgPacketLoss = packetLossTotal / float64(connectedPeers)
		avgJitter = jitterTotal / float64(connectedPeers)
	}

	return map[string]any{
		"matchId":                 room.matchID,
		"mode":                    room.mode,
		"crossRoleMode":           room.crossRoleMode,
		"fakeVoiceBankKey":        room.fakeVoiceBankKey,
		"contourHz":               room.contourHz,
		"activePeers":             activePeers,
		"connectedPeers":          connectedPeers,
		"speakingPeers":           speakingPeers,
		"liveSpeakers":            speakingPeers,
		"droppedPeers":            room.droppedPeers,
		"averagePacketLossPercent": avgPacketLoss,
		"averageJitterMilliseconds": avgJitter,
		"averageJitterMs":           avgJitter,
		"contourPacketsRelayed":     room.contourPacketsRelayed,
		"audioFramesRelayed":        room.audioFramesRelayed,
		"audioBytesRelayed":         room.audioBytesRelayed,
		"rawTrackSubscriptions":     rawSubscriptions,
		"contourOnlySubscriptions":  contourOnlySubscriptions,
		"updatedAtEpoch":            room.updatedAt.Unix(),
	}
}

func (room *roomState) maybePublishStats() {
	room.mu.RLock()
	lastPublished := room.lastStatsPublishedAt
	room.mu.RUnlock()
	if time.Since(lastPublished) < publishStatsMinInterval {
		return
	}

	payload := room.overviewStats()
	body, err := json.Marshal(payload)
	if err != nil {
		return
	}

	req, err := http.NewRequest(http.MethodPost, fmt.Sprintf("%s/api/matches/%s/voice/gateway-stats", worldServiceURL, room.matchID), bytes.NewReader(body))
	if err != nil {
		return
	}
	req.Header.Set("Content-Type", "application/json")
	if _, err := httpClient.Do(req); err != nil {
		return
	}

	room.mu.Lock()
	room.lastStatsPublishedAt = time.Now()
	room.mu.Unlock()
}

func (peer *peerState) snapshot() map[string]any {
	peer.mu.RLock()
	defer peer.mu.RUnlock()
	return map[string]any{
		"peerId":            peer.peerID,
		"displayName":       peer.displayName,
		"role":              peer.role,
		"teamId":            peer.teamID,
		"sessionId":         peer.sessionID,
		"connectedAtEpoch":  peer.connectedAt.Unix(),
		"packetLossPercent": peer.packetLossPercent,
		"jitterMs":          peer.jitterMS,
		"isSpeaking":        peer.isSpeaking,
		"hasInput":          peer.hasInput,
		"deviceLost":        peer.deviceLost,
		"roomMode":          peer.mode,
		"crossRoleMode":     peer.crossRoleMode,
	}
}

func (peer *peerState) sendJSON(message signalMessage) error {
	peer.mu.Lock()
	defer peer.mu.Unlock()
	return peer.ws.WriteJSON(message)
}

func (peer *peerState) addForwardedTrack(source *forwardedTrack) error {
	trackID := fmt.Sprintf("audio_%s", source.sourcePeerID)
	streamID := fmt.Sprintf("underfoot_%s", peer.matchID)
	localTrack, err := webrtc.NewTrackLocalStaticRTP(source.codec, trackID, streamID)
	if err != nil {
		return err
	}
	sender, err := peer.pc.AddTrack(localTrack)
	if err != nil {
		return err
	}
	go func() {
		rtcpBuf := make([]byte, 1500)
		for {
			if _, _, err := sender.Read(rtcpBuf); err != nil {
				return
			}
		}
	}()

	source.mu.Lock()
	source.subscribers[peer.peerID] = &forwardedSubscriber{
		targetPeerID: peer.peerID,
		track:        localTrack,
		sender:       sender,
	}
	source.mu.Unlock()
	return nil
}

func (peer *peerState) removeForwardedTrack(source *forwardedTrack) {
	source.mu.Lock()
	subscriber, ok := source.subscribers[peer.peerID]
	if ok {
		delete(source.subscribers, peer.peerID)
	}
	source.mu.Unlock()
	if ok {
		_ = peer.pc.RemoveTrack(subscriber.sender)
	}
}

func (room *roomState) syncForwardSubscriptions(sourcePeerID string) {
	room.mu.RLock()
	sourcePeer := room.peers[sourcePeerID]
	forward := room.forwards[sourcePeerID]
	targetPeers := make([]*peerState, 0, len(room.peers))
	for _, peer := range room.peers {
		targetPeers = append(targetPeers, peer)
	}
	room.mu.RUnlock()

	if sourcePeer == nil || forward == nil {
		return
	}

	for _, targetPeer := range targetPeers {
		if rawAudioAllowed(room, sourcePeer, targetPeer) {
			forward.mu.RLock()
			_, exists := forward.subscribers[targetPeer.peerID]
			forward.mu.RUnlock()
			if !exists {
				if err := targetPeer.addForwardedTrack(forward); err != nil {
					log.Printf("voice-gateway: add track %s -> %s failed: %v", sourcePeer.peerID, targetPeer.peerID, err)
					continue
				}
				go peerRenegotiate(targetPeer)
			}
			continue
		}
		targetPeer.removeForwardedTrack(forward)
	}
}

func peerRenegotiate(peer *peerState) {
	peer.mu.Lock()
	pc := peer.pc
	peer.mu.Unlock()
	if pc == nil {
		return
	}
	if pc.ConnectionState() == webrtc.PeerConnectionStateClosed {
		return
	}
	offer, err := pc.CreateOffer(nil)
	if err != nil {
		return
	}
	if err := pc.SetLocalDescription(offer); err != nil {
		return
	}
	gather := webrtc.GatheringCompletePromise(pc)
	<-gather
	local := pc.LocalDescription()
	if local == nil {
		return
	}
	_ = peer.sendJSON(signalMessage{
		Type:          "offer",
		SourcePeerID:  "sfu",
		SDP:           local.SDP,
		RoomMode:      peer.mode,
		CrossRoleMode: peer.crossRoleMode,
	})
}

func (room *roomState) registerPeer(peer *peerState) {
	room.mu.Lock()
	room.peers[peer.peerID] = peer
	room.updatedAt = time.Now()
	room.mu.Unlock()
	forwardIDs := room.forwardIDs()
	for _, sourceID := range forwardIDs {
		room.syncForwardSubscriptions(sourceID)
	}
	room.broadcast(signalMessage{
		Type:          "peer_joined",
		SourcePeerID:  peer.peerID,
		Peer:          peer.snapshot(),
		Stats:         room.overviewStats(),
		RoomMode:      room.mode,
		CrossRoleMode: room.crossRoleMode,
	}, peer.peerID)
	room.maybePublishStats()
}

func (room *roomState) unregisterPeer(peerID string) {
	room.mu.Lock()
	peer := room.peers[peerID]
	if peer != nil {
		delete(room.peers, peerID)
		room.droppedPeers++
		room.updatedAt = time.Now()
	}
	for _, forward := range room.forwards {
		if forward.sourcePeerID == peerID {
			delete(room.forwards, peerID)
			continue
		}
	}
	room.mu.Unlock()

	if peer != nil {
		for _, forwardID := range room.forwardIDs() {
			room.syncForwardSubscriptions(forwardID)
		}
		room.broadcast(signalMessage{
			Type:          "peer_left",
			SourcePeerID:  peerID,
			Stats:         room.overviewStats(),
			RoomMode:      room.mode,
			CrossRoleMode: room.crossRoleMode,
		}, "")
	}
	room.maybePublishStats()
}

func (room *roomState) forwardIDs() []string {
	room.mu.RLock()
	defer room.mu.RUnlock()
	ids := make([]string, 0, len(room.forwards))
	for id := range room.forwards {
		ids = append(ids, id)
	}
	return ids
}

func (room *roomState) ensureForward(sourcePeer *peerState, codec webrtc.RTPCodecCapability) *forwardedTrack {
	room.mu.Lock()
	defer room.mu.Unlock()
	forward, ok := room.forwards[sourcePeer.peerID]
	if !ok {
		forward = &forwardedTrack{
			sourcePeerID: sourcePeer.peerID,
			sourceRole:   sourcePeer.role,
			codec:        codec,
			subscribers:  map[string]*forwardedSubscriber{},
		}
		room.forwards[sourcePeer.peerID] = forward
	}
	return forward
}

func (room *roomState) fanoutContour(sourcePeer *peerState, packet contourPacket) {
	room.mu.RLock()
	targetPeers := make([]*peerState, 0, len(room.peers))
	for _, peer := range room.peers {
		if contourAllowed(room, sourcePeer, peer) {
			targetPeers = append(targetPeers, peer)
		}
	}
	room.mu.RUnlock()

	for _, targetPeer := range targetPeers {
		targetPeer.mu.RLock()
		dc := targetPeer.contourDC
		targetPeer.mu.RUnlock()
		if dc != nil && dc.ReadyState() == webrtc.DataChannelStateOpen {
			body, _ := json.Marshal(packet)
			_ = dc.SendText(string(body))
		} else {
			_ = targetPeer.sendJSON(signalMessage{
				Type:          "contour_packet",
				SourcePeerID:  sourcePeer.peerID,
				Packet:        &packet,
				RoomMode:      room.mode,
				CrossRoleMode: room.crossRoleMode,
			})
		}
	}

	room.mu.Lock()
	room.contourPacketsRelayed += int64(len(targetPeers))
	room.updatedAt = time.Now()
	room.mu.Unlock()
	room.maybePublishStats()
}

func (room *roomState) handleIncomingTrack(sourcePeer *peerState, remoteTrack *webrtc.TrackRemote) {
	forward := room.ensureForward(sourcePeer, remoteTrack.Codec().RTPCodecCapability)
	room.syncForwardSubscriptions(sourcePeer.peerID)
	for {
		packet, _, err := remoteTrack.ReadRTP()
		if err != nil {
			return
		}
		bytesRelayed := int64(packet.MarshalSize())
		deliveries := 0
		forward.mu.RLock()
		for _, subscriber := range forward.subscribers {
			if err := subscriber.track.WriteRTP(packet); err == nil {
				deliveries++
			}
		}
		forward.mu.RUnlock()

		room.mu.Lock()
		room.audioFramesRelayed += int64(deliveries)
		room.audioBytesRelayed += bytesRelayed * int64(deliveries)
		room.updatedAt = time.Now()
		room.mu.Unlock()
		room.maybePublishStats()
	}
}

func (room *roomState) broadcast(message signalMessage, excludePeerID string) {
	room.mu.RLock()
	targets := make([]*peerState, 0, len(room.peers))
	for _, peer := range room.peers {
		if excludePeerID != "" && peer.peerID == excludePeerID {
			continue
		}
		targets = append(targets, peer)
	}
	room.mu.RUnlock()

	for _, peer := range targets {
		if err := peer.sendJSON(message); err != nil {
			go room.unregisterPeer(peer.peerID)
		}
	}
}

func handleHealth(w http.ResponseWriter, r *http.Request) {
	rooms := voiceOverviewPayload()
	writeJSON(w, http.StatusOK, map[string]any{
		"status":       "ok",
		"project":      projectName,
		"gatewayPort":  voiceGatewayPort,
		"activeMatches": rooms["activeMatches"],
		"activePeers":   rooms["activePeers"],
	})
}

func voiceOverviewPayload() map[string]any {
	state.mu.RLock()
	rooms := make([]map[string]any, 0, len(state.rooms))
	activePeers := 0
	for _, room := range state.rooms {
		stats := room.overviewStats()
		rooms = append(rooms, stats)
		activePeers += int(stats["activePeers"].(int))
	}
	state.mu.RUnlock()
	return map[string]any{
		"status":        "ok",
		"matches":       rooms,
		"activeMatches": len(rooms),
		"activePeers":   activePeers,
		"worldServiceUrl": worldServiceURL,
		"turn": map[string]any{
			"host":    voiceTurnHost,
			"port":    voiceTurnPort,
			"tlsPort": voiceTurnTLSPort,
		},
	}
}

func handleOverview(w http.ResponseWriter, r *http.Request) {
	writeJSON(w, http.StatusOK, voiceOverviewPayload())
}

func handleMatchStats(w http.ResponseWriter, r *http.Request) {
	matchID := strings.TrimPrefix(r.URL.Path, "/api/matches/")
	matchID = strings.TrimSuffix(matchID, "/stats")
	room := ensureRoom(&tokenPayload{MatchID: matchID, Mode: "match_strict_illusion", CrossRoleMode: "contour_only", ContourHz: 20})
	writeJSON(w, http.StatusOK, map[string]any{"status": "ok", "stats": room.overviewStats()})
}

func handleMatchSessions(w http.ResponseWriter, r *http.Request) {
	matchID := strings.TrimPrefix(r.URL.Path, "/api/matches/")
	matchID = strings.TrimSuffix(matchID, "/sessions")
	room := ensureRoom(&tokenPayload{MatchID: matchID, Mode: "match_strict_illusion", CrossRoleMode: "contour_only", ContourHz: 20})
	room.mu.RLock()
	peers := make([]map[string]any, 0, len(room.peers))
	for _, peer := range room.peers {
		peers = append(peers, peer.snapshot())
	}
	room.mu.RUnlock()
	writeJSON(w, http.StatusOK, map[string]any{"status": "ok", "peers": peers, "stats": room.overviewStats()})
}

func handleSignaling(w http.ResponseWriter, r *http.Request) {
	matchID := strings.TrimPrefix(r.URL.Path, "/ws/signaling/")
	pathParts := strings.Split(strings.Trim(matchID, "/"), "/")
	if len(pathParts) != 2 {
		http.Error(w, "invalid signaling path", http.StatusBadRequest)
		return
	}
	matchID = pathParts[0]
	peerID := pathParts[1]

	token, err := decodeVoiceToken(r.URL.Query().Get("token"))
	if err != nil {
		http.Error(w, err.Error(), http.StatusForbidden)
		return
	}
	if token.MatchID != matchID || token.PlayerID != peerID {
		http.Error(w, "voice token match/player mismatch", http.StatusForbidden)
		return
	}

	ws, err := upgrader.Upgrade(w, r, nil)
	if err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}

	pc, err := newPeerConnection()
	if err != nil {
		_ = ws.Close()
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}

	room := ensureRoom(token)
	peer := &peerState{
		peerID:        token.PlayerID,
		displayName:   token.DisplayName,
		role:          strings.ToLower(strings.TrimSpace(token.Role)),
		teamID:        token.TeamID,
		sessionID:     token.SessionID,
		matchID:       token.MatchID,
		mode:          token.Mode,
		crossRoleMode: token.CrossRoleMode,
		ws:            ws,
		pc:            pc,
		connectedAt:   time.Now(),
	}

	pc.OnICECandidate(func(candidate *webrtc.ICECandidate) {
		if candidate == nil {
			return
		}
		jsonCandidate := candidate.ToJSON()
		sdpMid := ""
		if jsonCandidate.SDPMid != nil {
			sdpMid = *jsonCandidate.SDPMid
		}
		sdpMLineIndex := -1
		if jsonCandidate.SDPMLineIndex != nil {
			sdpMLineIndex = int(*jsonCandidate.SDPMLineIndex)
		}
		_ = peer.sendJSON(signalMessage{
			Type:          "ice_candidate",
			SourcePeerID:  "sfu",
			Candidate:     jsonCandidate.Candidate,
			SDPMid:        sdpMid,
			SDPMLineIndex: sdpMLineIndex,
			RoomMode:      room.mode,
			CrossRoleMode: room.crossRoleMode,
		})
	})

	pc.OnDataChannel(func(dc *webrtc.DataChannel) {
		if dc.Label() != "underfoot-contour" {
			return
		}
		peer.mu.Lock()
		peer.contourDC = dc
		peer.mu.Unlock()
		dc.OnMessage(func(message webrtc.DataChannelMessage) {
			packet := contourPacket{}
			if err := json.Unmarshal(message.Data, &packet); err != nil {
				return
			}
			if packet.PlayerID == "" {
				packet.PlayerID = peer.peerID
			}
			if packet.CreatedAtEpoch == 0 {
				packet.CreatedAtEpoch = nowEpoch()
			}
			room.fanoutContour(peer, packet)
		})
	})

	pc.OnTrack(func(track *webrtc.TrackRemote, receiver *webrtc.RTPReceiver) {
		if track.Kind() != webrtc.RTPCodecTypeAudio {
			return
		}
		go room.handleIncomingTrack(peer, track)
	})

	pc.OnConnectionStateChange(func(state webrtc.PeerConnectionState) {
		switch state {
		case webrtc.PeerConnectionStateClosed, webrtc.PeerConnectionStateFailed, webrtc.PeerConnectionStateDisconnected:
			room.unregisterPeer(peer.peerID)
		}
	})

	room.registerPeer(peer)
	_ = peer.sendJSON(signalMessage{
		Type:          "welcome",
		SourcePeerID:  "sfu",
		RoomMode:      room.mode,
		CrossRoleMode: room.crossRoleMode,
		Stats:         room.overviewStats(),
		Detail:        "underfoot voice gateway connected",
	})

	for {
		var incoming signalMessage
		if err := ws.ReadJSON(&incoming); err != nil {
			break
		}
		switch strings.ToLower(strings.TrimSpace(incoming.Type)) {
		case "offer":
			if err := pc.SetRemoteDescription(webrtc.SessionDescription{Type: webrtc.SDPTypeOffer, SDP: incoming.SDP}); err != nil {
				_ = peer.sendJSON(signalMessage{Type: "error", Detail: err.Error()})
				continue
			}
			answer, err := pc.CreateAnswer(nil)
			if err != nil {
				_ = peer.sendJSON(signalMessage{Type: "error", Detail: err.Error()})
				continue
			}
			if err := pc.SetLocalDescription(answer); err != nil {
				_ = peer.sendJSON(signalMessage{Type: "error", Detail: err.Error()})
				continue
			}
			gather := webrtc.GatheringCompletePromise(pc)
			<-gather
			local := pc.LocalDescription()
			if local != nil {
				_ = peer.sendJSON(signalMessage{
					Type:          "answer",
					SourcePeerID:  "sfu",
					SDP:           local.SDP,
					RoomMode:      room.mode,
					CrossRoleMode: room.crossRoleMode,
				})
			}
		case "answer":
			_ = pc.SetRemoteDescription(webrtc.SessionDescription{Type: webrtc.SDPTypeAnswer, SDP: incoming.SDP})
		case "ice_candidate":
			if incoming.Candidate == "" {
				continue
			}
			var sdpMid *string
			if incoming.SDPMid != "" {
				sdpMid = &incoming.SDPMid
			}
			var sdpMLineIndex *uint16
			if incoming.SDPMLineIndex >= 0 {
				value := uint16(incoming.SDPMLineIndex)
				sdpMLineIndex = &value
			}
			_ = pc.AddICECandidate(webrtc.ICECandidateInit{
				Candidate:     incoming.Candidate,
				SDPMid:        sdpMid,
				SDPMLineIndex: sdpMLineIndex,
			})
		case "speaking":
			peer.mu.Lock()
			peer.isSpeaking = incoming.IsSpeaking
			peer.hasInput = incoming.HasInput
			peer.mu.Unlock()
			room.maybePublishStats()
		case "stats":
			peer.mu.Lock()
			peer.packetLossPercent = incoming.PacketLossPercent
			peer.jitterMS = incoming.JitterMS
			peer.hasInput = incoming.HasInput
			peer.deviceLost = incoming.DeviceLost
			peer.mu.Unlock()
			room.maybePublishStats()
		case "contour_packet":
			if incoming.Packet != nil {
				packet := *incoming.Packet
				if packet.PlayerID == "" {
					packet.PlayerID = peer.peerID
				}
				if packet.CreatedAtEpoch == 0 {
					packet.CreatedAtEpoch = nowEpoch()
				}
				room.fanoutContour(peer, packet)
			}
		case "ping":
			_ = peer.sendJSON(signalMessage{Type: "pong", SourcePeerID: "sfu"})
		}
	}

	room.unregisterPeer(peer.peerID)
	_ = pc.Close()
	_ = ws.Close()
}

func writeJSON(w http.ResponseWriter, status int, payload any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(payload)
}

func main() {
	if !strings.EqualFold(strings.TrimSpace(os.Getenv("LUMINAX_RUNTIME_ENABLED")), "true") {
		log.Print("underfoot_voice_gateway: runtime_disabled")
		return
	}
	if len(voiceJoinSecret) < 32 {
		log.Fatal("underfoot_voice_gateway: voice_join_secret_not_configured")
	}
	mux := http.NewServeMux()
	mux.HandleFunc("/health", handleHealth)
	mux.HandleFunc("/api/voice/overview", handleOverview)
	mux.HandleFunc("/api/matches/", func(w http.ResponseWriter, r *http.Request) {
		switch {
		case strings.HasSuffix(r.URL.Path, "/stats"):
			handleMatchStats(w, r)
		case strings.HasSuffix(r.URL.Path, "/sessions"):
			handleMatchSessions(w, r)
		default:
			http.NotFound(w, r)
		}
	})
	mux.HandleFunc("/ws/signaling/", handleSignaling)

	server := &http.Server{
		Addr:              ":" + voiceGatewayPort,
		Handler:           mux,
		ReadHeaderTimeout: 10 * time.Second,
	}

	log.Printf("underfoot voice gateway listening on :%s", voiceGatewayPort)
	if err := server.ListenAndServe(); err != nil && err != http.ErrServerClosed {
		log.Fatal(err)
	}
}
