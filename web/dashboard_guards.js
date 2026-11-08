// 戰情頁純函式。不抓行情、不改 DOM 以外的狀態。
function displayChange(change, changePct) {
  if (change === null || change === undefined || changePct === null || changePct === undefined) {
    return { text: "漲跌未知", kind: "unknown" };
  }
  const ch = Number(change);
  const chPct = Number(changePct);
  if (!Number.isFinite(ch) || !Number.isFinite(chPct)) {
    return { text: "漲跌未知", kind: "unknown" };
  }
  if (ch > 0) return { text: `▲ +${ch.toFixed(2)} (+${chPct.toFixed(2)}%)`, kind: "up" };
  if (ch < 0) return { text: `▼ ${ch.toFixed(2)} (${chPct.toFixed(2)}%)`, kind: "down" };
  return { text: "0.00 (0.00%)", kind: "flat" };
}

function quoteHeadline(inferred) {
  if (!inferred || inferred.verified !== true || inferred.status !== "ok") {
    return "報價推斷：未知";
  }
  return `報價推斷：${inferred.label_display || inferred.label || "未知"}`;
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = { displayChange, quoteHeadline };
}
