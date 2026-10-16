FROM golang:1.24-alpine AS build

WORKDIR /src

COPY go.mod ./
COPY main.go ./
RUN go mod tidy
RUN CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -trimpath -ldflags="-s -w" -o /out/underfoot-voice-gateway ./main.go

FROM alpine:3.21

WORKDIR /app
RUN adduser -D -u 10001 appuser

COPY --from=build /out/underfoot-voice-gateway /app/underfoot-voice-gateway

USER appuser
EXPOSE 8470

ENTRYPOINT ["/app/underfoot-voice-gateway"]
