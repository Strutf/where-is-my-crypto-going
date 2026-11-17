// Funções comuns das páginas: tema, textos, formatação, leitura da API, ícones em pixel, gráfico
// de preço e a cena do personagem. Carregado no <head>; os scripts de cada página usam o que está
// em window.WIMCG.
(() => {
  const root = document.documentElement;
  const T = window.T || {};
  const LANG = root.dataset.lang || "en";
  const LOCALE = LANG === "pt" ? "pt-BR" : "en-US";
  const THEME_KEY = "wimcg-theme";

  const t = (key) => (key in T ? T[key] : key);
  const tok = (name) => getComputedStyle(root).getPropertyValue("--" + name).trim();

  // Tema: o aparelho decide; o botão força claro ou escuro e guarda a escolha.
  const isDark = () => root.dataset.theme ? root.dataset.theme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
  const themeListeners = [];
  function setTheme(mode) {
    root.dataset.theme = mode;
    try { localStorage.setItem(THEME_KEY, mode); } catch (e) { /* navegação privada */ }
    themeListeners.forEach((f) => f());
  }
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => themeListeners.forEach((f) => f()));

  const fmtUSD = (v, digits = 2) => "$" + Number(v).toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });
  const fmtPct = (v, digits = 2, sign = true) => (sign && v > 0 ? "+" : v < 0 ? "−" : "") + Math.abs(v).toFixed(digits) + "%";
  const pad = (n) => String(n).padStart(2, "0");
  function fmtTime(iso, opts = {}) {
    const d = new Date(iso);
    const date = d.toLocaleString(LOCALE, { month: "short", day: "numeric", timeZone: "UTC", ...(opts.year ? { year: "numeric" } : {}) });
    if (opts.dateOnly) return date;
    const hour = `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}`;
    return opts.hourOnly ? hour : `${date}, ${hour}`;
  }

  async function getJson(path) {
    const r = await fetch(path, { headers: { Accept: "application/json" } });
    if (!r.ok) throw new Error(`${path}: ${r.status}`);
    return r.json();
  }

  // Ícones: mapas de bits desenhados como retângulos SVG na cor do texto.
  const ICONS = {
    up: ["...##...", "..####..", ".######.", "########", "..####..", "..####..", "..####..", "..####.."],
    down: ["..####..", "..####..", "..####..", "..####..", "########", ".######.", "..####..", "...##..."],
    check: [".......#", "......##", ".....##.", "#...##..", "##.##...", ".###....", "..#.....", "........"],
    cross: ["##....##", "###..###", ".######.", "..####..", "..####..", ".######.", "###..###", "##....##"],
    coin: ["..####..", ".#....#.", "#.###..#", "#.#..#.#", "#.###..#", "#.#..#.#", "#.###..#", ".#....#.", "..####.."],
    clock: ["..####..", ".#....#.", "#..#...#", "#..#...#", "#..###.#", "#......#", ".#....#.", "..####.."],
    sun: ["...#....", ".#.#.#..", "..###...", "#######.", "..###...", ".#.#.#..", "...#....", "........"],
    moon: ["..####..", ".###....", "###.....", "###.....", "###.....", ".###....", "..####..", "........"],
  };
  function icon(name, scale = 3) {
    const rows = ICONS[name], h = rows.length, w = rows[0].length;
    let rects = "";
    rows.forEach((r, y) => [...r].forEach((c, x) => { if (c === "#") rects += `<rect x="${x}" y="${y}" width="1" height="1"/>`; }));
    return `<svg width="${w * scale}" height="${h * scale}" viewBox="0 0 ${w} ${h}" fill="currentColor" shape-rendering="crispEdges" aria-hidden="true">${rects}</svg>`;
  }

  // Gráfico de preço desenhado em 240 x 80 pontos e ampliado sem suavizar.
  class PixelChart {
    constructor(box) {
      this.box = box;
      this.cv = box.querySelector("canvas");
      this.ctx = this.cv.getContext("2d");
      this.tip = box.querySelector(".tip");
      this.W = this.cv.width; this.H = this.cv.height;
      this.hover = -1; this.cols = [];
      const move = (e) => this.onMove(e);
      this.cv.addEventListener("pointermove", move);
      this.cv.addEventListener("pointerdown", move);
      this.cv.addEventListener("pointerleave", (e) => { if (e.pointerType === "mouse") this.clearHover(); });
      themeListeners.push(() => this.draw());
      addEventListener("resize", () => this.draw());
    }
    // points: [{ time, close }] em ordem. Cada coluna mostra o último ponto do seu intervalo.
    setData(points, opts = {}) {
      this.points = points; this.opts = opts;
      const n = points.length, W = this.W;
      this.cols = [];
      for (let x = 0; x < W; x++) {
        const a = Math.floor(x * n / W), b = Math.max(a + 1, Math.floor((x + 1) * n / W));
        this.cols.push(points[Math.min(n - 1, b - 1)]);
      }
      const v = this.cols.map((p) => p.close);
      this.lo = Math.min(...v); this.hi = Math.max(...v);
      this.hover = -1;
      this.draw();
    }
    y(v) { return Math.round(3 + (this.hi - v) / ((this.hi - this.lo) || 1) * (this.H - 7)); }
    draw() {
      if (!this.cols.length) return;
      const { ctx, W, H } = this;
      ctx.fillStyle = tok("panel2"); ctx.fillRect(0, 0, W, H);
      ctx.fillStyle = tok("grid");
      for (let gy = 10; gy < H; gy += 16) for (let x = 0; x < W; x += 2) ctx.fillRect(x, gy, 1, 1);
      const line = tok("accent");
      ctx.fillStyle = line; ctx.globalAlpha = 0.35;
      for (let x = 0; x < W; x++) for (let y = this.y(this.cols[x].close) + 2; y < H; y++) if ((x + y) % 2 === 0) ctx.fillRect(x, y, 1, 1);
      ctx.globalAlpha = 1;
      for (let x = 0; x < W; x++) {
        const y1 = this.y(this.cols[x].close), y0 = x ? this.y(this.cols[x - 1].close) : y1;
        ctx.fillRect(x, Math.min(y0, y1), 1, Math.abs(y1 - y0) + 1);
      }
      ctx.fillRect(W - 3, this.y(this.cols[W - 1].close) - 1, 3, 3);
      if (this.hover >= 0) {
        ctx.fillStyle = tok("ink");
        for (let y = 0; y < H; y += 2) ctx.fillRect(this.hover, y, 1, 1);
        ctx.fillRect(this.hover - 1, this.y(this.cols[this.hover].close) - 1, 3, 3);
      }
      const yl = this.box.querySelectorAll(".yl");
      if (yl.length === 2) {
        yl[0].textContent = fmtUSD(this.hi, 0); yl[0].style.top = (this.cv.offsetTop - 2) + "px";
        yl[1].textContent = fmtUSD(this.lo, 0); yl[1].style.top = (this.cv.offsetTop + this.cv.offsetHeight - 20) + "px";
      }
      const xl = this.box.querySelector(".xl");
      if (xl) {
        const marks = [0, 60, 120, 180, W - 1];
        const hourly = this.opts.hourLabels;
        xl.innerHTML = marks.map((i) => `<span>${fmtTime(this.cols[i].time, hourly ? { hourOnly: true } : { dateOnly: true })}</span>`).join("");
      }
    }
    onMove(e) {
      const r = this.cv.getBoundingClientRect();
      const x = Math.max(0, Math.min(this.W - 1, Math.floor((e.clientX - r.left) / r.width * this.W)));
      this.hover = x; this.draw();
      const p = this.cols[x];
      this.tip.hidden = false;
      this.tip.innerHTML = `${fmtTime(p.time)} UTC<br><span style="color:var(--accent)">${fmtUSD(p.close)}</span>`;
      const box = this.box.getBoundingClientRect();
      const px = r.left - box.left + (x + 0.5) / this.W * r.width;
      const py = r.top - box.top + this.y(p.close) / this.H * r.height;
      const w = this.tip.offsetWidth, h = this.tip.offsetHeight;
      const left = px + 14 + w > box.width - 8 ? Math.max(8, px - w - 14) : px + 14;
      this.tip.style.left = left + "px";
      this.tip.style.top = Math.max(4, py - h - 12) + "px";
    }
    clearHover() { this.hover = -1; this.tip.hidden = true; this.draw(); }
  }

  // Cena ao lado do gráfico: neve caindo (mercado em baixa) ou poeira e vapor (em alta).
  class Scene {
    constructor(box) {
      this.cv = box.querySelector("canvas"); this.ctx = this.cv.getContext("2d");
      this.W = this.cv.width; this.H = this.cv.height; this.mode = "bear"; this.tick = 0; this.last = 0;
      this.reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
      this.flakes = Array.from({ length: 46 }, (_, i) => ({ x: (i * 37) % this.W, y: (i * 53) % this.H, v: 0.4 + (i % 5) * 0.18, s: i % 7 === 0 ? 2 : 1, ph: i }));
      this.puffs = Array.from({ length: 14 }, (_, i) => ({ x: 10 + (i * 29) % 60, y: this.H - 8, age: (i * 7) % 40, s: 1 + (i % 3) }));
      themeListeners.push(() => this.draw());
      if (!this.reduce) requestAnimationFrame((t) => this.loop(t));
      else this.draw();
    }
    setMode(mode) { this.mode = mode; this.draw(); }
    loop(t) {
      if (!document.hidden && t - this.last > 83) { this.tick++; this.last = t; this.step(); this.draw(); }
      requestAnimationFrame((u) => this.loop(u));
    }
    step() {
      for (const f of this.flakes) { f.y += f.v; if (f.y > this.H - 6) { f.y = -2; f.x = (f.x + 23) % this.W; } }
      for (const p of this.puffs) { p.age += 1; if (p.age > 40) { p.age = 0; p.x = 8 + ((p.x + 17) % 64); } }
    }
    draw() {
      const { ctx, W, H, tick } = this;
      ctx.fillStyle = tok("panel2"); ctx.fillRect(0, 0, W, H);
      if (this.mode === "bear") {
        ctx.fillStyle = tok("frost");
        for (const f of this.flakes) {
          const x = Math.round(f.x + Math.sin((tick + f.ph * 9) / 18) * 2);
          ctx.fillRect((x + W) % W, Math.round(f.y), f.s, f.s);
        }
        for (let x = 0; x < W; x++) { const h = 4 + Math.round(2 * Math.sin(x / 6) + (x % 11 === 0 ? 1 : 0)); ctx.fillRect(x, H - h, 1, h); }
      } else {
        ctx.fillStyle = tok("muted");
        for (let x = 0; x < W; x++) ctx.fillRect(x, H - 4, 1, 4);
        ctx.fillStyle = tok("accent");
        for (const p of this.puffs) {
          const a = p.age / 40;
          if (this.reduce || (tick + p.x) % 3 !== 0) ctx.fillRect(Math.round(p.x + a * 6), Math.round(p.y - a * 18), p.s, p.s);
        }
      }
    }
  }

  // Cartões das previsões mais recentes (página inicial e página de previsões).
  function forecastCards(rows) {
    return rows.map((r) => {
      const cls = r.direction === "up" ? "up" : "down";
      const on = Math.round(r.prob_up * 10);
      const blocks = Array.from({ length: 10 }, (_, i) => `<i style="${i < on ? "background:var(--up)" : ""}"></i>`).join("");
      return `<div class="card px inset">
        <span class="lbl">${t("in_hours").replace("{h}", r.horizon_hours)}</span>
        <span class="dir ${cls}">${icon(r.direction, 4)} ${t(r.direction)}</span>
        <div class="bar" role="img" aria-label="${(r.prob_up * 100).toFixed(1)}% ${t("chance_up")}">${blocks}</div>
        <span class="meta"><b>${(r.prob_up * 100).toFixed(1)}%</b> ${t("chance_up")}<br>
        <b>±${r.pred_volatility_pct.toFixed(2)}%</b> ${t("expected_swing")}<br>
        ${t("from_price")} <b>${fmtUSD(r.base_price)}</b><br>
        ${t("result_at")} <b>${fmtTime(r.target_time)}</b> UTC</span>
      </div>`;
    }).join("");
  }

  // Botão de tema e menu: marcados aqui para valer em todas as páginas.
  document.addEventListener("DOMContentLoaded", () => {
    const btn = document.getElementById("theme-btn");
    if (btn) {
      const paint = () => { btn.innerHTML = icon(isDark() ? "sun" : "moon", 2); btn.setAttribute("aria-label", t(isDark() ? "theme_light" : "theme_dark")); };
      btn.addEventListener("click", () => { setTheme(isDark() ? "light" : "dark"); paint(); });
      themeListeners.push(paint);
      paint();
    }
    document.querySelectorAll("[data-icon]").forEach((el) => { el.innerHTML = icon(el.dataset.icon, Number(el.dataset.scale || 3)); });
  });

  window.WIMCG = { t, tok, LANG, LOCALE, fmtUSD, fmtPct, fmtTime, getJson, icon, forecastCards, PixelChart, Scene, onTheme: (f) => themeListeners.push(f) };
})();
