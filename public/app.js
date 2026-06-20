/*
 * Radikale Venstre resolutions demo frontend.
 *
 * The application is intentionally dependency-free: it loads a static JSON file,
 * builds filters in memory and renders the result list directly in the browser.
 * This keeps the prototype easy to host on GitHub Pages, Codeberg Pages or any
 * ordinary static web server.
 *
 * Data contract
 * -------------
 * New generated data should use the canonical wrapper format:
 *
 *   { "schema_version": 1, "source_documents": [...], "resolutions": [...] }
 *
 * For convenience during prototyping, the code also accepts the old format where
 * `resolutions.json` is simply an array of resolution objects.
 */

const state = {
  data: null,
  query: "",
  year: "all",
  policyArea: "all",
  status: "current",
};

// Centralised DOM references make the rest of the code easier to scan.
const els = {
  search: document.querySelector("#search"),
  year: document.querySelector("#year"),
  chapter: document.querySelector("#chapter"),
  status: document.querySelector("#status"),
  reset: document.querySelector("#reset"),
  summary: document.querySelector("#summary"),
  summaryMobile: document.querySelector("#summaryMobile"),
  results: document.querySelector("#results"),
  copyLink: document.querySelector("#copyLink"),
  copyLinkMobile: document.querySelector("#copyLinkMobile"),
  dialog: document.querySelector("#detailsDialog"),
  closeDialog: document.querySelector("#closeDialog"),
  detailMeta: document.querySelector("#detailMeta"),
  detailTitle: document.querySelector("#detailTitle"),
  detailBody: document.querySelector("#detailBody"),
  detailKeywords: document.querySelector("#detailKeywords"),
};

// Danish collation gives more natural sorting of æ/ø/å in policy areas/titles.
const collator = new Intl.Collator("da", { sensitivity: "base" });
const searchSuggestionsMedia = window.matchMedia("(max-width: 768px), (pointer: coarse)");
const searchSuggestionsListId = "searchSuggestions";

function shouldDisableNativeSearchSuggestions() {
  // Native datalist suggestions behave inconsistently on touch/mobile browsers.
  // Treat them as a desktop enhancement, not a core search feature.
  return searchSuggestionsMedia.matches;
}

function configureSearchSuggestions() {
  if (shouldDisableNativeSearchSuggestions()) {
    els.search.removeAttribute("list");
  } else {
    els.search.setAttribute("list", searchSuggestionsListId);
  }
}

function normalise(text) {
  /*
   * Normalise text for simple full-text search.
   *
   * This is not a real search engine. It is a transparent, dependency-free
   * search helper that is good enough for the first demo. A later version can
   * replace scoreResolution() with FlexSearch/Lunr without changing the data.
   */
  return (text || "")
    .toLocaleLowerCase("da-DK")
    .replace(/[^a-zæøå0-9\s-]/gi, " ")
    .replace(/\s+/g, " ")
    .trim();
}

function isShortSearchTerm(term) {
  // Very short terms should match word prefixes, not unrelated substrings.
  return term.length <= 2;
}

function searchTokens(text) {
  return normalise(text).split(/[^\p{L}\p{N}]+/u).filter(Boolean);
}

function containsSearchTerm(normalisedText, term) {
  if (!isShortSearchTerm(term)) return normalisedText.includes(term);
  return searchTokens(normalisedText).some(token => token.startsWith(term));
}

function searchTermIndex(normalisedText, term) {
  if (!term) return -1;
  if (!isShortSearchTerm(term)) return normalisedText.indexOf(term);

  const safe = term.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const match = new RegExp(`(^|[^\\p{L}\\p{N}])(${safe})`, "u").exec(normalisedText);
  if (!match) return -1;
  return match.index + match[1].length;
}

function escapeHtml(text) {
  // Avoid accidental HTML injection when rendering source text and titles.
  return String(text ?? "").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;"}[c]));
}

function escapeAttribute(text) {
  // Attribute values are double-quoted, but escaping apostrophes too is safer.
  return escapeHtml(text).replace(/'/g, "&#39;");
}

function validityDates(resolution) {
  // The prototype treats validity as date-based only, matching the yearly import workflow.
  return {
    from: new Date(`${resolution.valid_from}T00:00:00`),
    until: new Date(`${resolution.valid_until}T23:59:59`),
  };
}

function isActive(resolution, today = new Date()) {
  const { from, until } = validityDates(resolution);
  return from <= today && today <= until;
}

function expiresThisYear(resolution, today = new Date()) {
  const { until } = validityDates(resolution);
  return until.getFullYear() === today.getFullYear();
}


function statusBadges(resolution, today = new Date()) {
  const active = isActive(resolution, today);
  const expiresInCurrentYear = expiresThisYear(resolution, today);
  const badges = [
    `<span class="${active ? "valid" : "expired"}">${active ? "gældende" : "ikke gældende"}</span>`,
  ];

  if (expiresInCurrentYear) {
    badges.push(`<span class="expires-this-year">${active ? "udløber i år" : "udløb i år"}</span>`);
  }

  return badges.join(" · ");
}

function matchesStatus(resolution, status, today = new Date()) {
  switch (status) {
    case "all":
      return true;
    case "current":
      return isActive(resolution, today);
    case "expired":
      return validityDates(resolution).until < today;
    case "expires-this-year":
      return expiresThisYear(resolution, today);
    default:
      return isActive(resolution, today);
  }
}

function scoreResolution(r, terms) {
  /*
   * Return 0 for non-matches and a higher number for more relevant matches.
   *
   * The scoring is intentionally simple and explainable:
   * - exact local code matches are strongest
   * - title matches are stronger than body matches
   * - generated keywords/search terms help users find concepts not phrased
   *   exactly like the resolution title
   */
  if (terms.length === 0) return 1;

  const title = normalise(r.title);
  const body = normalise(r.body);
  const code = normalise(r.code);
  const policyArea = normalise(r.policy_area || r.chapter_title || r.local_chapter_title || "Ukendt");
  const keywords = normalise([...(r.keywords || []), ...(r.generated_search_terms || [])].join(" "));
  const fields = [code, title, policyArea, body, keywords];

  let score = 0;
  for (const term of terms) {
    if (!fields.some(field => containsSearchTerm(field, term))) return 0;
    if (code === term) score += 60;
    if (containsSearchTerm(title, term)) score += 25;
    if (containsSearchTerm(keywords, term)) score += 14;
    if (containsSearchTerm(policyArea, term)) score += 8;
    if (containsSearchTerm(body, term)) score += 4;
  }
  return score;
}

function makeExcerpt(text, terms, maxLen = 260) {
  // Show a short body excerpt, preferably centred around the first search hit.
  const plain = text.replace(/\s+/g, " ").trim();
  if (!plain) return "";

  const n = normalise(plain);
  let idx = -1;
  for (const term of terms) {
    idx = searchTermIndex(n, term);
    if (idx >= 0) break;
  }

  const start = idx > 70 ? idx - 70 : 0;
  let excerpt = plain.slice(start, start + maxLen);
  if (start > 0) excerpt = `…${excerpt}`;
  if (start + maxLen < plain.length) excerpt = `${excerpt}…`;
  return highlight(escapeHtml(excerpt), terms);
}

function highlight(html, terms) {
  // Highlight plain search terms inside already-escaped HTML snippets.
  if (terms.length === 0) return html;
  const unique = [...new Set(terms)].filter(t => t.length > 1).sort((a, b) => b.length - a.length);
  let out = html;
  for (const term of unique) {
    const safe = term.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    if (isShortSearchTerm(term)) {
      out = out.replace(new RegExp(`(^|[^\\p{L}\\p{N}])(${safe})`, "giu"), "$1<mark>$2</mark>");
    } else {
      out = out.replace(new RegExp(`(${safe})`, "giu"), "<mark>$1</mark>");
    }
  }
  return out;
}

function termsFromQuery(query) {
  return normalise(query).split(" ").filter(Boolean);
}

function applyUrlParams() {
  // Keep search/filter state in the URL so searches can be bookmarked/shared.
  const params = new URLSearchParams(location.search);
  state.query = params.get("q") || "";
  state.year = params.get("year") || "all";
  state.policyArea = params.get("policy_area") || params.get("chapter") || "all";

  state.status = params.get("status") || "current";
}

function updateUrl() {
  const params = new URLSearchParams();
  if (state.query) params.set("q", state.query);
  if (state.year !== "all") params.set("year", state.year);
  if (state.policyArea !== "all") params.set("policy_area", state.policyArea);
  if (state.status !== "current") params.set("status", state.status);
  history.replaceState(null, "", `${location.pathname}${params.toString() ? "?" + params.toString() : ""}`);
}

function populateFilters() {
  const years = [...new Set(state.data.resolutions.map(r => r.year))].sort((a, b) => b - a);
  els.year.innerHTML = `<option value="all">Alle år</option>` + years
    .map(y => `<option value="${escapeAttribute(y)}">${escapeHtml(y)}</option>`)
    .join("");

  // Policy areas are derived from canonical policy_area values, not the source
  // chapter titles. This keeps the UI stable even when chapter wording differs
  // slightly between years.
  els.chapter.innerHTML = `<option value="all">Alle politikområder</option>` + state.data.policyAreas
    .sort((a, b) => collator.compare(a.title, b.title))
    .map(c => `<option value="${escapeHtml(c.code)}">${escapeHtml(c.title)}</option>`).join("");

  els.search.value = state.query;
  els.year.value = state.year;
  els.chapter.value = state.policyArea;
  els.status.value = state.status;
}

function filteredResults() {
  const terms = termsFromQuery(state.query);
  return state.data.resolutions
    .map(r => ({ r, score: scoreResolution(r, terms) }))
    .filter(x => x.score > 0)
    .filter(x => state.year === "all" || String(x.r.year) === state.year)
    .filter(x => state.policyArea === "all" || x.r.policy_area === state.policyArea)
    .filter(x => matchesStatus(x.r, state.status))
    .sort((a, b) => b.score - a.score || a.r.year - b.r.year || collator.compare(a.r.code, b.r.code))
    .map(x => x.r);
}

function render() {
  updateUrl();
  const terms = termsFromQuery(state.query);
  const results = filteredResults();
  const total = state.data.resolutions.length;
  const summaryText = `${results.length} af ${total} resolutioner vises`;
  els.summary.textContent = summaryText;
  els.summaryMobile.textContent = summaryText;

  if (results.length === 0) {
    els.results.innerHTML = `<div class="empty">Ingen resolutioner matcher filtrene.</div>`;
    return;
  }

  els.results.innerHTML = results.map(r => {
    const tags = [...(r.keywords || [])].slice(0, 5).map(k => `<span class="tag">${escapeHtml(k)}</span>`).join("");
    return `<article class="result-card" data-id="${escapeAttribute(r.id)}" role="button" tabindex="0" aria-label="Vis resolution: ${escapeAttribute(r.title)}">
      <p class="meta">${escapeHtml(r.code)} · ${escapeHtml(r.policy_area || r.chapter_title)} · ${escapeHtml(r.valid_from)}–${escapeHtml(r.valid_until)} · ${statusBadges(r)}</p>
      <h2 class="result-title">${highlight(escapeHtml(r.title), terms)}</h2>
      <p class="excerpt">${makeExcerpt(r.body, terms)}</p>
      <div class="tags">${tags}</div>
    </article>`;
  }).join("");
}

function openDetails(id) {
  const r = state.data.resolutions.find(item => item.id === id);
  if (!r) return;

  els.detailMeta.innerHTML = `${escapeHtml(r.code)} · ${escapeHtml(r.policy_area || r.chapter_title)} · ${escapeHtml(r.valid_from)}–${escapeHtml(r.valid_until)} · ${statusBadges(r)}`;
  els.detailTitle.textContent = r.title;
  els.detailBody.textContent = r.body;

  const allTags = [...(r.keywords || []), ...(r.generated_search_terms || [])];
  els.detailKeywords.innerHTML = allTags.map(k => `<span class="tag">${escapeHtml(k)}</span>`).join("");
  els.dialog.showModal();
}

function wireEvents() {
  // Every UI change updates state, rerenders the list and refreshes the URL state.
  if (searchSuggestionsMedia.addEventListener) {
    searchSuggestionsMedia.addEventListener("change", configureSearchSuggestions);
  } else {
    searchSuggestionsMedia.addListener(configureSearchSuggestions);
  }

  els.search.addEventListener("input", e => { state.query = e.target.value; render(); });
  els.year.addEventListener("change", e => { state.year = e.target.value; render(); });
  els.chapter.addEventListener("change", e => { state.policyArea = e.target.value; render(); });
  els.status.addEventListener("change", e => { state.status = e.target.value; render(); });

  els.reset.addEventListener("click", () => {
    state.query = "";
    state.year = "all";
    state.policyArea = "all";
    state.status = "current";
    populateFilters();
    render();
  });

  els.results.addEventListener("click", e => {
    const card = e.target.closest(".result-card[data-id]");
    if (!card) return;

    // Let users select/copy text from a card without opening the dialog.
    if (window.getSelection().toString()) return;

    openDetails(card.dataset.id);
  });

  els.results.addEventListener("keydown", e => {
    const card = e.target.closest(".result-card[data-id]");
    if (!card || !["Enter", " "].includes(e.key)) return;

    e.preventDefault();
    openDetails(card.dataset.id);
  });

  els.closeDialog.addEventListener("click", () => els.dialog.close());

  els.copyLink.addEventListener("click", () => shareCurrentLink(els.copyLink, "Kopiér link"));
  els.copyLinkMobile.addEventListener("click", () => shareCurrentLink(els.copyLinkMobile, "Del link"));
}


async function shareCurrentLink(button, defaultText) {
  try {
    if (navigator.share) {
      await navigator.share({
        title: document.title,
        url: location.href,
      });
      return;
    }

    await navigator.clipboard.writeText(location.href);
    button.textContent = "Link kopieret";
    setTimeout(() => button.textContent = defaultText, 1400);
  } catch (err) {
    // AbortError is the normal result when the native share dialog is cancelled.
    if (err && err.name === "AbortError") return;

    button.textContent = "Kunne ikke dele";
    setTimeout(() => button.textContent = defaultText, 1400);
  }
}

function normaliseData(raw) {
  /*
   * Accept both the canonical wrapper and the old raw-array format.
   *
   * The UI wants a list of resolutions and a list of filterable chapters. The
   * parser now writes the canonical wrapper, but this compatibility layer keeps
   * older generated files working during prototype iteration.
   */
  const resolutions = Array.isArray(raw) ? raw : (raw.resolutions || []);
  const policyAreaMap = new Map();

  for (const r of resolutions) {
    if (!r.policy_area) {
      console.warn("Resolution is missing policy_area", r);
      continue;
    }
    const policyArea = r.policy_area;
    if (!policyAreaMap.has(policyArea)) {
      policyAreaMap.set(policyArea, { code: policyArea, title: policyArea });
    }
  }

  return {
    metadata: Array.isArray(raw) ? {} : raw,
    resolutions,
    policyAreas: [...policyAreaMap.values()],
  };
}

async function init() {
  applyUrlParams();
  const response = await fetch("resolutions.json");
  const raw = await response.json();
  state.data = normaliseData(raw);
  populateFilters();
  configureSearchSuggestions();
  wireEvents();
  render();
}

init().catch(err => {
  console.error(err);
  els.summary.textContent = "Kunne ikke indlæse data.";
  els.summaryMobile.textContent = "Kunne ikke indlæse data.";
});
