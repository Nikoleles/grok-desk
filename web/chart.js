/* Hand-drawn charts. No chart library. */
(function () {
  function setup(canvas) {
    const dpr = window.devicePixelRatio || 1;
    const w = canvas.clientWidth || 0;
    const h = canvas.clientHeight || 0;
    if (w < 2 || h < 2) return null;
    const pw = Math.round(w * dpr);
    const ph = Math.round(h * dpr);
    if (canvas.width !== pw || canvas.height !== ph) {
      canvas.width = pw;
      canvas.height = ph;
    }
    const ctx = canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);
    return { ctx, w, h };
  }

  function drawLine(canvas, points, marks, startValue) {
    const view = setup(canvas);
    if (!view) return;
    const { ctx, w, h } = view;
    const pad = { l: 8, r: 8, t: 18, b: 12 };
    if (!points || points.length < 2) {
      ctx.save();
      ctx.strokeStyle = "rgba(235,235,245,0.35)";
      ctx.setLineDash([4, 5]);
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(pad.l, h / 2);
      ctx.lineTo(w - pad.r, h / 2);
      ctx.stroke();
      ctx.restore();
      return;
    }
    const values = points.map((p) => Number(p.v));
    let min = Math.min(startValue, ...values);
    let max = Math.max(startValue, ...values);
    if (max - min < 1e-9) {
      min -= 1;
      max += 1;
    }
    const slack = (max - min) * 0.16;
    min -= slack;
    max += slack;
    const innerW = w - pad.l - pad.r;
    const innerH = h - pad.t - pad.b;
    const xAt = (i) => pad.l + (points.length === 1 ? innerW / 2 : (i / (points.length - 1)) * innerW);
    const yAt = (v) => pad.t + (1 - (v - min) / (max - min)) * innerH;

    const baseY = yAt(startValue);
    ctx.save();
    ctx.strokeStyle = "rgba(235,235,245,0.3)";
    ctx.setLineDash([4, 5]);
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(pad.l, baseY);
    ctx.lineTo(w - pad.r, baseY);
    ctx.stroke();
    ctx.restore();

    ctx.beginPath();
    points.forEach((p, i) => {
      const x = xAt(i);
      const y = yAt(Number(p.v));
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    ctx.lineTo(xAt(points.length - 1), h - pad.b);
    ctx.lineTo(xAt(0), h - pad.b);
    ctx.closePath();
    const grad = ctx.createLinearGradient(0, pad.t, 0, h - pad.b);
    grad.addColorStop(0, "rgba(0,211,90,0.28)");
    grad.addColorStop(1, "rgba(0,211,90,0)");
    ctx.fillStyle = grad;
    ctx.fill();

    ctx.beginPath();
    points.forEach((p, i) => {
      const x = xAt(i);
      const y = yAt(Number(p.v));
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    ctx.strokeStyle = "#00d35a";
    ctx.lineWidth = 2;
    ctx.lineJoin = "round";
    ctx.lineCap = "round";
    ctx.stroke();

    const t0 = Date.parse(points[0].t);
    const t1 = Date.parse(points[points.length - 1].t);
    const span = Math.max(1, t1 - t0);
    ctx.font = "11px Inter, -apple-system, sans-serif";
    const placed = [];
    const ordered = (marks || []).slice().sort((a, b) => Date.parse(a.t) - Date.parse(b.t));
    ordered.forEach((mark) => {
      const ts = Date.parse(mark.t);
      if (Number.isNaN(ts)) return;
      const label = String(mark.label || "");
      const width = Math.min(ctx.measureText(label).width + 14, 128);
      let x = pad.l + ((ts - t0) / span) * innerW;
      x = Math.max(pad.l + width / 2, Math.min(w - pad.r - width / 2, x));
      let row = 0;
      while (placed.some((p) => p.row === row && Math.abs(p.x - x) < (p.w + width) / 2 + 22)) row += 1;
      placed.push({ row, x, w: width, mark, label });
    });
    const maxRow = placed.reduce((m, p) => Math.max(m, p.row), -1);
    if (maxRow >= 0) {
      // Redraw the line lower so every mark has its own row.
      const extra = (maxRow + 1) * 20 + 6;
      ctx.clearRect(0, 0, w, h);
      const pad2 = { l: pad.l, r: pad.r, t: extra, b: pad.b };
      const innerH2 = h - pad2.t - pad2.b;
      const y2 = (v) => pad2.t + (1 - (v - min) / (max - min)) * innerH2;
      const baseY2 = y2(startValue);
      ctx.save();
      ctx.strokeStyle = "rgba(235,235,245,0.3)";
      ctx.setLineDash([4, 5]);
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(pad2.l, baseY2);
      ctx.lineTo(w - pad2.r, baseY2);
      ctx.stroke();
      ctx.restore();
      ctx.beginPath();
      points.forEach((p, i) => {
        const x = xAt(i);
        const y = y2(Number(p.v));
        if (i === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
      });
      ctx.lineTo(xAt(points.length - 1), h - pad2.b);
      ctx.lineTo(xAt(0), h - pad2.b);
      ctx.closePath();
      const grad2 = ctx.createLinearGradient(0, pad2.t, 0, h - pad2.b);
      grad2.addColorStop(0, "rgba(0,211,90,0.28)");
      grad2.addColorStop(1, "rgba(0,211,90,0)");
      ctx.fillStyle = grad2;
      ctx.fill();
      ctx.beginPath();
      points.forEach((p, i) => {
        const x = xAt(i);
        const y = y2(Number(p.v));
        if (i === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
      });
      ctx.strokeStyle = "#00d35a";
      ctx.lineWidth = 2;
      ctx.lineJoin = "round";
      ctx.lineCap = "round";
      ctx.stroke();
    }
    ctx.font = "11px Inter, -apple-system, sans-serif";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    placed.forEach((item) => {
      const y = 12 + item.row * 20;
      const color = item.mark.kind === "down" ? "#ff453a" : item.mark.kind === "buy" ? "#0a84ff" : "#00d35a";
      const bg = item.mark.kind === "down" ? "rgba(255,69,58,0.16)" : item.mark.kind === "buy" ? "rgba(10,132,255,0.18)" : "rgba(0,211,90,0.16)";
      const left = item.x - item.w / 2;
      roundRect(ctx, left, y - 8, item.w, 16, 8);
      ctx.fillStyle = bg;
      ctx.fill();
      ctx.fillStyle = color;
      ctx.fillText(item.label, item.x, y, item.w - 10);
    });
  }

  function drawCandles(canvas, candles, exitPrice) {
    const view = setup(canvas);
    if (!view) return;
    const { ctx, w, h } = view;
    if (!candles || !candles.length) {
      ctx.strokeStyle = "rgba(235,235,245,0.35)";
      ctx.setLineDash([4, 5]);
      ctx.beginPath();
      ctx.moveTo(12, h / 2);
      ctx.lineTo(w - 12, h / 2);
      ctx.stroke();
      return;
    }
    const pad = { l: 8, r: 8, t: 16, b: 8 };
    const volH = 46;
    const gap = 8;
    const priceBottom = h - pad.b - volH - gap;
    let min = Math.min(...candles.map((c) => c.l));
    let max = Math.max(...candles.map((c) => c.h));
    if (exitPrice != null) {
      min = Math.min(min, exitPrice);
      max = Math.max(max, exitPrice);
    }
    if (max - min < 1e-12) {
      min *= 0.98;
      max *= 1.02;
    }
    const slack = (max - min) * 0.08;
    min -= slack;
    max += slack;
    const yAt = (v) => pad.t + (1 - (v - min) / (max - min)) * (priceBottom - pad.t);
    const slot = (w - pad.l - pad.r) / candles.length;
    const bodyW = Math.max(2, Math.min(8, slot * 0.62));

    candles.forEach((c, i) => {
      const x = pad.l + slot * i + slot / 2;
      const up = c.c >= c.o;
      const color = up ? "#00d35a" : "#ff453a";
      ctx.strokeStyle = color;
      ctx.fillStyle = color;
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(x, yAt(c.h));
      ctx.lineTo(x, yAt(c.l));
      ctx.stroke();
      const top = yAt(Math.max(c.o, c.c));
      const bot = yAt(Math.min(c.o, c.c));
      const bh = Math.max(1, bot - top);
      ctx.fillRect(x - bodyW / 2, top, bodyW, bh);
    });

    if (exitPrice != null) {
      const y = yAt(exitPrice);
      ctx.save();
      ctx.strokeStyle = "#ff9f0a";
      ctx.setLineDash([5, 4]);
      ctx.lineWidth = 1.25;
      ctx.beginPath();
      ctx.moveTo(pad.l, y);
      ctx.lineTo(w - pad.r, y);
      ctx.stroke();
      ctx.restore();
      ctx.font = "11px Inter, -apple-system, sans-serif";
      ctx.fillStyle = "#ff9f0a";
      ctx.textAlign = "right";
      ctx.textBaseline = "bottom";
      ctx.fillText("Risk trailing stop", w - pad.r, Math.max(14, y - 4));
    }

    const maxV = Math.max(...candles.map((c) => Number(c.v) || 0), 1);
    candles.forEach((c, i) => {
      const x = pad.l + slot * i + slot / 2;
      const vh = ((Number(c.v) || 0) / maxV) * (volH - 4);
      const up = c.c >= c.o;
      ctx.fillStyle = up ? "rgba(0,211,90,0.45)" : "rgba(255,69,58,0.45)";
      ctx.fillRect(x - bodyW / 2, h - pad.b - vh, bodyW, vh);
    });
  }

  function roundRect(ctx, x, y, w, h, r) {
    ctx.beginPath();
    ctx.moveTo(x + r, y);
    ctx.arcTo(x + w, y, x + w, y + h, r);
    ctx.arcTo(x + w, y + h, x, y + h, r);
    ctx.arcTo(x, y + h, x, y, r);
    ctx.arcTo(x, y, x + w, y, r);
    ctx.closePath();
  }

  window.DeskChart = { drawLine, drawCandles };
})();
