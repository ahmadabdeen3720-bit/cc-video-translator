let currentId = null;
let cues = [];
let ccTimer = null;

const videoFile = document.getElementById("videoFile");
const btnUpload = document.getElementById("btnUpload");
const btnCC = document.getElementById("btnCC");
const btnDownload = document.getElementById("btnDownload");
const statusEl = document.getElementById("status");
const player = document.getElementById("player");

const outputEl = document.getElementById("output");
const langEl = document.getElementById("lang");

const ccBox = document.getElementById("ccBox");
const ccText = document.getElementById("ccText");
const toggleBox = document.getElementById("toggleBox");
const i18n = {
  ar: {
    title: "CC داخل الفيديو",
    upload: "رفع",
    makeCC: "عمل CC",
    download: "تحميل الفيديو مع الترجمة",
    output: "المخرجات",
    ready: "جاهز"
  },
  he: {
    title: "כתוביות בתוך הווידאו",
    upload: "העלה",
    makeCC: "צור כתוביות",
    download: "הורדת וידאו עם כתוביות",
    output: "פלט",
    ready: "מוכן"
  },
  en: {
    title: "Video Captions",
    upload: "Upload",
    makeCC: "Generate CC",
    download: "Download Video with Subtitles",
    output: "Output",
    ready: "Ready"
  }
};
function setLang(lang){
  const t = i18n[lang];
  document.documentElement.lang = lang;
  document.documentElement.dir = (lang === "ar" || lang === "he") ? "rtl" : "ltr";

  document.querySelector("h1").textContent = t.title;
  btnUpload.textContent = t.upload;
  btnCC.textContent = t.makeCC;
  btnDownload.textContent = t.download;
  statusEl.textContent = t.ready;
}

function setStatus(text, ok=true){
  statusEl.textContent = text;
  statusEl.style.background = ok ? "rgba(34,197,94,.12)" : "rgba(239,68,68,.12)";
  statusEl.style.borderColor = ok ? "rgba(34,197,94,.25)" : "rgba(239,68,68,.25)";
  statusEl.style.color = ok ? "#bff3cf" : "#ffd0d0";
}

toggleBox.addEventListener("change", () => {
  ccBox.style.display = toggleBox.checked ? "flex" : "none";
});

btnUpload.addEventListener("click", async () => {
  if (!videoFile.files || !videoFile.files[0]) {
    setStatus("اختار فيديو أولاً", false);
    return;
  }

  setStatus("برفع الفيديو...");
  btnUpload.disabled = true;
  btnCC.disabled = true;
  btnDownload.disabled = true;

  const fd = new FormData();
  fd.append("video", videoFile.files[0]);

  try{
    const res = await fetch("/api/upload", { method:"POST", body: fd });
    const j = await res.json();
    if (!j.ok) throw new Error(j.error || "Upload failed");

    currentId = j.id;
    player.src = `/api/video/${currentId}`;
    player.load();

    setStatus("تم رفع الفيديو ✅");
    btnCC.disabled = false;
  }catch(e){
    setStatus("فشل الرفع: " + e.message, false);
  }finally{
    btnUpload.disabled = false;
  }
});

btnCC.addEventListener("click", async () => {
  if (!currentId) return;

  setStatus("بعمل CC عربي ممتاز + عبري...");
  btnCC.disabled = true;
  btnDownload.disabled = true;

  try{
    const body = {
      id: currentId,
      output: outputEl.value,
      language: (langEl.value || "ar").trim() || "ar"
    };

    const res = await fetch("/api/transcribe", {
      method:"POST",
      headers: { "Content-Type":"application/json" },
      body: JSON.stringify(body)
    });

    const j = await res.json();
    if (!j.ok) throw new Error(j.error || "CC failed");

    await loadVtt(j.vtt);
    setStatus("CC جاهز ✅");
    startCcLoop();

    btnDownload.disabled = false; // ✅ صار ممكن تحميل الفيديو
  }catch(e){
    setStatus("فشل CC: " + e.message, false);
  }finally{
    btnCC.disabled = false;
  }
});

btnDownload.addEventListener("click", () => {
  if (!currentId) return;
  // يحمل MP4 مع ترجمة محروقة
  window.location.href = `/api/render/${currentId}`;
});

async function loadVtt(url){
  const res = await fetch(url);
  const text = await res.text();
  cues = parseVtt(text);
}

function startCcLoop(){
  if (ccTimer) clearInterval(ccTimer);

  ccTimer = setInterval(() => {
    if (!toggleBox.checked) return;
    const t = player.currentTime || 0;
    const cue = cues.find(c => t >= c.start && t <= c.end);
    if (cue && cue.text) {
      ccText.textContent = cue.text;
      ccBox.style.opacity = "1";
    } else {
      ccText.textContent = "";
      ccBox.style.opacity = "0";
    }
  }, 120);
}

function parseTime(ts){
  const [hms, ms] = ts.split(".");
  const [h,m,s] = hms.split(":").map(Number);
  const milli = Number(ms || 0);
  return (h*3600) + (m*60) + s + (milli/1000);
}

function parseVtt(vtt){
  const lines = vtt.replace(/\r/g,"").split("\n");
  const out = [];
  let i = 0;

  while (i < lines.length && lines[i].trim() !== "") i++;
  while (i < lines.length && lines[i].trim() === "") i++;

  while (i < lines.length){
    if (/^\d+$/.test(lines[i].trim())) i++;
    if (i >= lines.length) break;

    const timeLine = (lines[i] || "").trim();
    const m = timeLine.match(/^(\d{2}:\d{2}:\d{2}\.\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}\.\d{3})/);
    if (!m) { i++; continue; }

    const start = parseTime(m[1]);
    const end = parseTime(m[2]);
    i++;

    const textParts = [];
    while (i < lines.length && lines[i].trim() !== ""){
      textParts.push(lines[i]);
      i++;
    }

    out.push({ start, end, text: textParts.join("\n").trim() });
    while (i < lines.length && lines[i].trim() === "") i++;
  }

  return out;
}
setLang("ar"); // اللغة الافتراضية
