const $ = (id) => document.getElementById(id);
let packages = {};

function setStatus(msg, bad) {
  $("status").textContent = msg;
  $("status").style.color = bad ? "#b91c1c" : "#166534";
}

async function loadPosts() {
  const base = $("api").value.replace(/\/$/, "");
  $("posts").innerHTML = "";
  $("preview").textContent = "";
  packages = {};
  try {
    const res = await fetch(`${base}/api/posts?status=READY_TO_PUBLISH`);
    if (!res.ok) throw new Error(`API answered ${res.status}`);
    const posts = await res.json();
    if (!posts.length) return setStatus("No posts are ready. Approve and 'Prepare to publish' in the dashboard first.", true);
    for (const p of posts) {
      const r = await fetch(`${base}/api/posts/${p.id}/package`);
      if (!r.ok) continue; // approval changed since; skip it
      packages[p.id] = await r.json();
      const opt = document.createElement("option");
      opt.value = p.id;
      opt.textContent = `${p.platform.toUpperCase()}: ${(p.title || p.id).slice(0, 40)}`;
      $("posts").appendChild(opt);
    }
    showPreview();
    setStatus(`${Object.keys(packages).length} post(s) loaded.`);
  } catch (e) {
    setStatus(`Cannot reach the local API (${e.message}). Is run_api.py running?`, true);
  }
}

function showPreview() {
  const pkg = packages[$("posts").value];
  $("preview").textContent = pkg ? pkg.parts[0] : "";
}

// Runs inside the page. Returns a message; never clicks any button.
function fillComposer(platform, text) {
  const host = location.hostname;
  const ok = (platform === "linkedin" && host.endsWith("linkedin.com")) ||
             (platform === "x" && (host.endsWith("x.com") || host.endsWith("twitter.com")));
  if (!ok) return { ok: false, msg: `This tab is ${host}, but the post is for ${platform}.` };
  const selectors = platform === "linkedin"
    ? ['div.ql-editor[contenteditable="true"]', '[role="textbox"][contenteditable="true"]']
    : ['[data-testid="tweetTextarea_0"]', 'div[role="textbox"][contenteditable="true"]'];
  let box = null;
  for (const s of selectors) { box = document.querySelector(s); if (box) break; }
  if (!box) return { ok: false, msg: "Could not find the post box. Open the composer first." };
  box.focus();
  document.execCommand("selectAll", false, null);
  const done = document.execCommand("insertText", false, text);
  if (!done) return { ok: false, msg: "The page refused the text. Paste it manually." };
  return { ok: true, msg: "Text inserted. Check it, attach the image, then press Post yourself." };
}

$("reload").addEventListener("click", loadPosts);
$("posts").addEventListener("change", showPreview);
$("fill").addEventListener("click", async () => {
  const pkg = packages[$("posts").value];
  if (!pkg) return setStatus("Choose a post first.", true);
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    const [{ result }] = await chrome.scripting.executeScript({
      target: { tabId: tab.id }, func: fillComposer, args: [pkg.platform, pkg.parts[0]],
    });
    setStatus(result.msg, !result.ok);
  } catch (e) {
    setStatus(`Failed: ${e.message}`, true);
  }
});
loadPosts();
