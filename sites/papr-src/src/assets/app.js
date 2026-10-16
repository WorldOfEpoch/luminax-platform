const observer = new IntersectionObserver(
  (entries) => {
    entries.forEach((entry) => {
      if (entry.isIntersecting) {
        entry.target.classList.add("is-visible");
        observer.unobserve(entry.target);
      }
    });
  },
  { threshold: 0.14 }
);

document.querySelectorAll("[data-reveal]").forEach((element) => observer.observe(element));

const navToggle = document.querySelector("[data-nav-toggle]");
const nav = document.querySelector("[data-nav]");

if (navToggle && nav) {
  navToggle.addEventListener("click", () => {
    const isOpen = nav.classList.toggle("is-open");
    navToggle.setAttribute("aria-expanded", String(isOpen));
  });
}

document.querySelectorAll(".button").forEach((button) => {
  button.addEventListener("click", (event) => {
    const ripple = document.createElement("span");
    ripple.className = "ripple";
    const rect = button.getBoundingClientRect();
    const size = Math.max(rect.width, rect.height);
    ripple.style.width = `${size}px`;
    ripple.style.height = `${size}px`;
    ripple.style.left = `${event.clientX - rect.left - size / 2}px`;
    ripple.style.top = `${event.clientY - rect.top - size / 2}px`;
    button.appendChild(ripple);
    window.setTimeout(() => ripple.remove(), 520);
  });
});

document.querySelectorAll(".faq-item").forEach((item) => {
  const toggle = item.querySelector(".faq-toggle");
  const panel = item.querySelector(".faq-panel");
  if (!toggle || !panel) {
    return;
  }

  toggle.addEventListener("click", () => {
    const isOpen = item.classList.toggle("is-open");
    toggle.setAttribute("aria-expanded", String(isOpen));
    panel.style.maxHeight = isOpen ? `${panel.scrollHeight + 4}px` : "0px";
  });
});

document.querySelectorAll("[data-filter-group]").forEach((group) => {
  const buttons = group.querySelectorAll("[data-filter]");
  const cards = document.querySelectorAll(group.dataset.filterTarget);

  buttons.forEach((button) => {
    button.addEventListener("click", () => {
      const filter = button.dataset.filter;
      buttons.forEach((candidate) => candidate.classList.toggle("is-active", candidate === button));

      cards.forEach((card) => {
        const matches = filter === "all" || card.dataset.status === filter;
        card.hidden = !matches;
      });
    });
  });
});
