/* The service owns view state and every expansion decision. */
"use strict";
const $ = (id) => document.getElementById(id);
const state = {
  reader: null,
  locale: "en",
  instances: [],
  view: null,
  latest: null,
  root: null,
  history: [],
  nodes: [],
  anchors: [],
  lines: [],
  edges: [],
  selected: null,
  hovered: null,
  detail: "summary",
  detailCursor: null,
  job: null,
  jobTarget: null,
  busy: false,
  epoch: 0,
};
const el = (tag, className, text) => {
  const e = document.createElement(tag);
  if (className) e.className = className;
  if (text !== undefined) e.textContent = text;
  return e;
};
const button = (text, callback, className = "") => {
  const b = el("button", className, text);
  b.type = "button";
  b.onclick = callback;
  return b;
};
function notice(message, error = false, recovery = null) {
  const box = $("notice");
  box.replaceChildren();
  box.hidden = !message;
  box.className = error ? "error" : "";
  if (message) box.append(el("span", "", message));
  if (recovery)
    box.append(button("Return to latest", () => loadView(recovery)));
}
async function api(tool, args) {
  const response = await fetch(`/api/${tool}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(args),
  });
  const result = await response.json();
  if (!response.ok || result.ok === false) {
    const error = Object.assign(
      new Error(result.error?.message || `Request failed (${response.status})`),
      result.error,
    );
    if (error.latest_view) state.latest = error.latest_view;
    throw error;
  }
  return result;
}
function guarded(fn) {
  return async (...args) => {
    try {
      return await fn(...args);
    } catch (error) {
      notice(error.message, true, error.latest_view);
    }
  };
}
function args(extra = {}) {
  return { reader_id: state.reader, view_id: state.view, ...extra };
}
function setBusy(busy) {
  state.busy = busy;
  $("document").setAttribute("aria-busy", String(busy));
  $("document").classList.toggle("loading", busy);
  updateControls();
}
function updateControls() {
  const old = state.view !== state.latest;
  $("latest").hidden = !old;
  $("back").disabled = state.busy || state.history.length < 2;
  $("reset").disabled = state.busy || old;
  $("budget-save").disabled = state.busy || old;
  $("locale").disabled = state.busy || old;
  $("view-status").textContent = state.busy
    ? tr("Loading view…", "正在加载视图…")
    : old
      ? tr("Previous view · read only", "历史视图 · 只读")
      : tr("Current view · saved", "当前视图 · 已保存");
  document
    .querySelectorAll("[data-action]")
    .forEach((b) => (b.disabled = state.busy || old || Boolean(state.job)));
}
async function paged(tool, view, epoch) {
  let cursor;
  const pages = [];
  do {
    const page = await api(tool, {
      reader_id: state.reader,
      view_id: view,
      limit: 200,
      ...(cursor ? { cursor } : {}),
    });
    if (epoch !== state.epoch) return null;
    if (page.view_id !== view)
      throw new Error(
        "The server returned a different view. Reload this view.",
      );
    pages.push(page);
    cursor = page.next_cursor;
  } while (cursor);
  return pages;
}
async function loadView(view, { remember = true, target = null } = {}) {
  const focused = document.activeElement?.dataset.target;
  const previousNodes = new Map(state.nodes.map((node) => [node.id, node]));
  const previousSelected = state.selected;
  const epoch = ++state.epoch;
  setBusy(true);
  try {
    const [texts, maps] = await Promise.all([
      paged("read_text", view, epoch),
      paged("get_overview", view, epoch),
    ]);
    if (epoch !== state.epoch || !texts || !maps) return;
    state.view = view;
    state.locale = texts[0].locale || "en";
    $("locale").value = state.locale;
    $("instances").value = texts[0].instance_id;
    document.documentElement.lang = state.locale;
    localize();
    if (remember && state.history.at(-1) !== view) state.history.push(view);
    state.nodes = [
      ...new Map(maps.flatMap((p) => p.nodes).map((n) => [n.id, n])).values(),
    ];
    state.nodes.filter(n => n.kind === "external").forEach(n => { n.title = `${n.repo_key} ${tr("interfaces", "接口")}`; });
    if (previousSelected && !state.nodes.some((node) => node.id === previousSelected)) {
      let ancestor = previousNodes.get(previousSelected)?.parent;
      while (ancestor && !state.nodes.some((node) => node.id === ancestor))
        ancestor = previousNodes.get(ancestor)?.parent;
      state.selected = ancestor || state.root;
    }
    state.edges = [
      ...new Map(maps.flatMap((p) => p.edges).map((e) => [e.id, e])).values(),
    ];
    const length = texts[0].length || maps[0].length;
    if (length) {
      $("budget").value = length.budget_codepoints ?? "";
      $("length").textContent =
        tr(`${length.total_codepoints.toLocaleString()} codepoints (text + titles; not reading time)${length.over_budget ? " · over budget" : ""}`, `${length.total_codepoints.toLocaleString()} 码点（正文与标题；非阅读时间）${length.over_budget ? " · 超出预算" : ""}`);
      $("length").title = "Unicode codepoints, not reading time";
      $("length").classList.toggle("over-budget", length.over_budget);
    }
    state.anchors = [
      ...new Map(
        texts.flatMap((p) => p.anchors).map((a) => [a.anchor_id, a]),
      ).values(),
    ];
    state.lines = [];
    for (const page of texts) {
      const lines = page.text.split("\n");
      for (let line = page.start_line; line <= page.end_line; line++)
        state.lines[line - 1] = lines[line - page.start_line] ?? "";
    }
    $("title").textContent = state.nodes.find(n => n.id === state.root)?.title || $("title").textContent;
    const instance = state.instances.find(item => item.instance_id === texts[0].instance_id);
    if (instance) {
      $("scope-label").hidden = instance.declaration_count === undefined;
      $("scope-label").textContent = instance.declaration_count === undefined ? "" : tr(`${instance.declaration_count} declarations`, `${instance.declaration_count} 个声明`);
      if (texts[0].content_complete === false) {
        $("scope-label").textContent += tr(` · Partial text (${texts[0].published_node_count}/${texts[0].node_count} entries)`, ` · 部分正文（${texts[0].published_node_count}/${texts[0].node_count} 项）`);
      }
      const option = [...$("instances").options].find(option => option.value === instance.instance_id);
      if (option) option.textContent = `${$("title").textContent} · ${instance.locale || tr("default", "默认")}`;
    }
    document.querySelectorAll(".hovered").forEach(element => element.classList.remove("hovered"));
    renderInlineMath($("title"));
    renderDocument();
    if (focused) {
      [...document.querySelectorAll("[data-target]")]
        .find((e) => e.dataset.target === focused)
        ?.focus({ preventScroll: true });
    }
    renderGraph();
    renderHDG();
    renderBreadcrumbs(state.selected || state.root);
    $("line-count").textContent = tr(`${texts[0].total_lines} logical lines`, `${texts[0].total_lines} 个逻辑行`);
    if (state.view === state.latest) await recommendations();
    else
      $("recommendations").replaceChildren(
        el("p", "empty", tr("Suggestions belong to the latest view.", "推荐属于最新视图。")),
      );
    if (target) scrollToAnchor(target);
    if (state.selected) {
      if (state.detail === "edge") {
        if (state.edges.some((e) => e.id === state.selected))
          await inspectEdge(state.selected);
        else {
          state.selected = null;
          $("detail-content").replaceChildren();
          $("detail-tabs").hidden = true;
          $("detail-title").textContent =
            "Select a dependency in the current view.";
        }
      } else await showDetail(state.detail);
    }
    notice("");
  } finally {
    if (epoch === state.epoch) setBusy(false);
  }
}
function markdown(text, container) {
  const math = [];
  // Preserve TeX escapes before Markdown can consume set braces or line breaks.
  const protectedText = text.replace(
    /(?<!\\)\$\$[\s\S]*?(?<!\\)\$\$|(?<!\\)\$(?:\\.|[^$\\\n])*?\$|\\\([\s\S]*?\\\)|\\\[[\s\S]*?\\\]/g,
    (value) => {
      const token = `LEANMATHTOKEN${math.length}END`;
      math.push(value);
      return token;
    },
  );
  const html = marked.parse(protectedText, { gfm: true, breaks: false });
  container.innerHTML = DOMPurify.sanitize(html, {
    FORBID_TAGS: ["img", "style", "iframe", "form", "input", "button"],
    FORBID_ATTR: ["style"],
  });
  const walker = document.createTreeWalker(container, NodeFilter.SHOW_TEXT);
  while (walker.nextNode()) {
    walker.currentNode.textContent = walker.currentNode.textContent.replace(
      /LEANMATHTOKEN(\d+)END/g,
      (match, index) => math[Number(index)] ?? match,
    );
  }
  container.querySelectorAll("a").forEach((a) => {
    a.target = "_blank";
    a.rel = "noopener noreferrer";
  });
  renderMathInElement(container, {
    delimiters: [
      { left: "$$", right: "$$", display: true },
      { left: "\\[", right: "\\]", display: true },
      { left: "$", right: "$", display: false },
      { left: "\\(", right: "\\)", display: false },
    ],
    throwOnError: false,
    trust: false,
  });
}
function renderDocument() {
  const article = $("document");
  const activeJob = $("job");
  if (activeJob && article.contains(activeJob)) $("notice").after(activeJob);
  const shells = new Map(
    [...article.querySelectorAll(".section-shell")].map((e) => [
      e.dataset.node,
      e,
    ]),
  );
  const segments = new Map(
    [...article.querySelectorAll(".segment")].map((e) => [e.id, e]),
  );
  const nodes = new Map(
    state.nodes.filter((n) => n.kind !== "external").map((n) => [n.id, n]),
  );
  const anchorMap = new Map(state.anchors.map((a) => [a.anchor_id, a]));
  const childMap = new Map();
  for (const n of nodes.values()) {
    if (!childMap.has(n.parent)) childMap.set(n.parent, []);
    childMap.get(n.parent).push(n);
  }
  const position = (n) => anchorMap.get(n.anchor)?.start_line ?? Infinity;
  for (const children of childMap.values())
    children.sort((a, b) => position(a) - position(b));
  function segment(node, part) {
    const anchor = anchorMap.get(`${node.id}:${part}`);
    if (!anchor || anchor.end_line < anchor.start_line) return null;
    const text = state.lines
      .slice(anchor.start_line - 1, anchor.end_line)
      .join("\n");
    if (!text.trim()) return null;
    const block =
      segments.get(anchor.anchor_id) || el("div", `segment ${part}`);
    block.id = anchor.anchor_id;
    block.dataset.node = node.id;
    block.dataset.part = part;
    const boundaryLabels = {
      lead_in: tr("intro", "引入"),
      synopsis: tr("overview", "概要"),
      lead_out: tr("close", "收束"),
    };
    block.dataset.boundaryLabel = boundaryLabels[part] || "";
    if (block.dataset.markdown !== text || block.dataset.locale !== state.locale) {
      block.replaceChildren();
      const content = el("div", "prose");
      markdown(text, content);
      block.append(content);
      block.dataset.markdown = text;
      block.dataset.locale = state.locale;
    }
    block.onmouseenter = () => highlight(node.id, true);
    block.onmouseleave = () => highlight(node.id, false);
    block.onclick = guarded(() => select(node.id, false));
    return block;
  }
  function build(node, depth) {
    const shell = shells.get(node.id) || el("section", "section-shell");
    shell.id = `${node.id}:section`;
    shell.dataset.node = node.id;
    shell.dataset.depth = depth;
    shell.style.setProperty("--depth",depth);
    shell.style.setProperty("--rail-offset", `${Math.min(depth, 6) * 18}px`);
    shell.title = depth > 4 ? node.title : "";
    const heading = el("div", "section-heading");
    const rail = el("div", "section-rail");
    const railLabel = el("div", "rail-label");
    if (node.can_expand || node.can_collapse) {
      const action = node.can_collapse ? "collapse" : "expand";
      const toggle = button(
        node.can_collapse ? "▾" : "▸",
        guarded(() => act(action, node.id)),
        "toggle",
      );
      toggle.dataset.action = action;
      toggle.dataset.target = node.id;
      toggle.setAttribute(
        "aria-label",
        `${action === "expand" ? "Expand" : "Collapse"} ${node.title}`,
      );
      toggle.setAttribute("aria-expanded", String(node.can_collapse));
      railLabel.append(toggle);
    }
    const title = button(node.title, guarded(() => select(node.id, false)), "node-title rail-title");
    title.title = node.title;
    railLabel.append(title);
    rail.append(railLabel);
    const content = [rail, heading];
    const append = (part) => {
      const block = segment(node, part);
      if (block) content.push(block);
    };
    if (node.kind === "unit") {
      append("statement");
      append("proof");
      append("content");
    } else {
      append("lead_in");
      const body =
        document.getElementById(`${node.id}:body`) || el("div", "section-body");
      body.id = `${node.id}:body`;
      const children = childMap.get(node.id) || [];
      body.replaceChildren();
      if (children.length)
        body.append(...children.map((child) => build(child, depth + 1)));
      else {
        const synopsis = segment(node, "synopsis");
        if (synopsis) body.append(synopsis);
      }
      if (body.childElementCount) content.push(body);
      append("lead_out");
    }
    shell.replaceChildren(...content);
    shell.classList.toggle("selected", node.id === state.selected);
    return shell;
  }
  article.replaceChildren(
    ...(childMap.get(null) || []).map((n) => build(n, 0)),
  );
  article.querySelectorAll(".node-title").forEach(renderInlineMath);
  requestAnimationFrame(balanceMarginHeadings);
}
function highlight(id, on) {
  state.hovered = on ? id : state.hovered === id ? null : state.hovered;
  document.querySelectorAll("[data-node]").forEach((e) => {
    if (e.dataset.node === id) e.classList.toggle("hovered", on);
  });
  document.querySelectorAll(".hdg-edge").forEach((edge) => {
    if (edge.dataset.provider === id || edge.dataset.consumer === id)
      edge.classList.toggle("incident", on || state.selected === id);
  });
}
function renderGraph() {
  const graph = $("graph");
  graph.replaceChildren();
  const internal = state.nodes.filter((n) => n.kind !== "external");
  $("node-count").textContent = tr(`${internal.length} visible`, `${internal.length} 个可见节点`);
  const children = new Map();
  internal.forEach((n) => {
    if (!children.has(n.parent)) children.set(n.parent, []);
    children.get(n.parent).push(n);
  });
  function tree(node, depth = 0) {
    const wrap = el(
      "div",
      node.state === "expanded" ? "graph-scope" : "graph-item",
    );
    const b = button(
      "",
      guarded(() => select(node.id, true)),
      "graph-node",
    );
    b.dataset.node = node.id;
    b.classList.toggle("selected", state.selected === node.id);
    b.append(
      el("small", "", `${kindLabel(node.raw_kind || node.kind)} · ${tr(node.state, ({expanded:"已展开",collapsed:"已折叠",terminal:"终端"})[node.state] || node.state)}`),
      el("span", "", node.title),
    );
    b.onmouseenter = () => highlight(node.id, true);
    b.onmouseleave = () => highlight(node.id, false);
    wrap.append(b);
    const nested = children.get(node.id) || [];
    if (nested.length) {
      const body = el("div", depth < 2 ? "graph-children" : "");
      nested.forEach((n) => body.append(tree(n, depth + 1)));
      wrap.append(body);
    }
    return wrap;
  }
  (children.get(null) || []).forEach((n) => graph.append(tree(n)));
  graph.querySelectorAll(".graph-node span").forEach(renderInlineMath);
  const outside = $("external");
  outside.replaceChildren();
  const external = state.nodes.filter((n) => n.kind === "external");
  if (external.length) {
    const details = el("details", "external-list");
    details.append(el("summary", "", tr(`${external.length} external repository interfaces`, `${external.length} 个外部项目接口`)));
    external.forEach((n) =>
      details.append(
        button(
          n.title,
          guarded(() => select(n.id, false)),
        ),
      ),
    );
    outside.append(details);
  }
}
function renderBreadcrumbs(id) {
  const byId = new Map(state.nodes.map((n) => [n.id, n])),
    chain = [];
  let node = byId.get(id);
  while (node) {
    chain.unshift(node);
    node = byId.get(node.parent);
  }
  const box = $("breadcrumbs");
  box.replaceChildren();
  chain.forEach((n, i) => {
    if (i) box.append(el("span", "", "/"));
    box.append(
      button(
        n.parent ? n.title : tr("Library", "文库"),
        guarded(() => select(n.id, true)),
      ),
    );
  });
  box.querySelectorAll("button").forEach(renderInlineMath);
}
function scrollToAnchor(anchor) {
  const element = document.getElementById(anchor);
  if (element) {
    element.scrollIntoView({ behavior: "smooth", block: "start" });
    element.classList.remove("flash");
    requestAnimationFrame(() => element.classList.add("flash"));
  }
}
async function select(id, locate) {
  state.selected = id;
  focusGraph(id);
  renderBreadcrumbs(id);
  document
    .querySelectorAll("[data-node]")
    .forEach((e) => e.classList.toggle("selected", e.dataset.node === id));
  document.querySelectorAll(".hdg-edge").forEach((edge) =>
    edge.classList.toggle("incident", edge.dataset.provider === id || edge.dataset.consumer === id),
  );
  if (locate) {
    const result = await api("locate", args({ ref: id }));
    if (result.anchor) scrollToAnchor(result.anchor);
    if (result.expand_path?.length)
      notice(
        "This declaration is inside a collapsed section. Open the highlighted section to read it.",
      );
  }
  await showDetail("summary");
}
async function inspectEdge(id, cursor = null) {
  state.selected = id;
  document.querySelectorAll("[data-node], [data-edge]").forEach(e => e.classList.toggle("selected", e.dataset.edge === id));
  state.detail = "edge";
  const view = state.view;
  const result = await api(
    "inspect",
    args({ ref: id, detail: "summary", ...(cursor ? { cursor } : {}) }),
  );
  if (state.selected !== id || state.view !== view) return;
  $("detail-tabs").hidden = true;
  $("detail-title").textContent = tr("Dependency evidence", "依赖证据");
  const box = $("detail-content");
  if (!cursor) {
    box.replaceChildren();
    const count = result.edge?.evidence_count;
    if (count)
      box.append(
        el(
          "p",
          "empty",
          tr(`${count} original dependency record${count === 1 ? "" : "s"}`, `${count} 条原始依赖记录`),
        ),
      );
  }
  const edges = result.items || [result.edge];
  for (const edge of edges) {
    const card = el("div", "edge-card");
    for (const role of ["provider", "consumer"]) {
      const ref = edge[role + "_decl"];
      const row = el("div", "edge-endpoint");
      row.append(
        el("small", "", role === "provider" ? tr("PROVIDER", "依赖提供者") : tr("CONSUMER", "依赖使用者")),
      );
      row.append(
        button(
          ref.local_id,
          guarded(async () => {
            const location = await api("locate", args({ ref }));
            if (location.anchor) scrollToAnchor(location.anchor);
            state.selected = ref;
            await showDetail("summary");
            if (location.expand_path?.length)
              notice(
                tr("This declaration is inside the highlighted collapsed section.", "此声明位于高亮的折叠章节内。"),
              );
          }),
        ),
        el("span", "edge-repo", ref.repo_key),
      );
      card.append(row);
      if (role === "provider")
        card.append(el("div", "edge-arrow", tr("↓ supplies a dependency for", "↓ 为下方声明提供依赖")));
    }
    const sources = el("details", "edge-source");
    sources.append(el("summary", "", tr("Lean source", "Lean 源码")));
    const sourceBody = el("div");
    sources.append(sourceBody);
    sources.ontoggle = guarded(async () => {
      if (!sources.open || sources.dataset.loaded) return;
      sources.dataset.loaded = "loading";
      sourceBody.textContent = tr("Loading source…", "正在加载源码…");
      try {
        const items = [];
        for (const role of ["provider", "consumer"]) {
          let cursor;
          do {
            const page = await api("inspect", {
              reader_id: state.reader,
              view_id: view,
              ref: edge[role + "_decl"],
              detail: "lean",
              limit: 200,
              ...(cursor ? { cursor } : {}),
            });
            items.push(...page.items);
            cursor = page.next_cursor;
          } while (cursor);
        }
        sourceBody.replaceChildren();
        renderSourceRows(sourceBody, items);
        sources.dataset.loaded = "true";
      } catch (error) {
        delete sources.dataset.loaded;
        sourceBody.textContent = error.message;
      }
    });
    const raw = el("details", "edge-source");
    raw.append(
      el("summary", "", tr("Raw record", "原始记录")),
      el("pre", "", JSON.stringify(edge, null, 2)),
    );
    card.append(sources, raw);
    box.append(card);
  }
  state.detailCursor = result.next_cursor;
  $("detail-more").hidden = !result.next_cursor;
}
function renderSourceRows(box, items) {
  for (const item of items) {
    const key = JSON.stringify([item.ref, item.part]);
    let block = [...box.querySelectorAll("[data-source-key]")].find(
      (e) => e.dataset.sourceKey === key,
    );
    if (!block) {
      box.append(
        el("div", "part-label", `${item.ref.local_id} · ${item.part}`),
      );
      block = el("pre");
      block.dataset.sourceKey = key;
      box.append(block);
    }
    if (Number(block.dataset.sourceRows || 0) > 0) block.textContent += "\n";
    block.textContent += item.text ?? item.reason ?? "Source unavailable";
    block.dataset.sourceRows = String(
      Number(block.dataset.sourceRows || 0) + 1,
    );
  }
}
function addMember(box, item) {
  box.append(
    button(
      `${item.repo_key}: ${item.local_id}`,
      guarded(async () => {
        const located = await api("locate", args({ ref: item }));
        if (located.anchor) scrollToAnchor(located.anchor);
        state.selected = item;
        await showDetail("lean");
      }),
    ),
  );
}
async function showDetail(detail, cursor = null) {
  if (!state.selected) return;
  state.detail = detail;
  const ref = state.selected,
    view = state.view;
  const result = await api(
    "inspect",
    args({ ref, detail, ...(cursor ? { cursor } : {}) }),
  );
  if (ref !== state.selected || view !== state.view) return;
  $("detail-tabs").hidden = false;
  $("detail-tabs")
    .querySelectorAll("button")
    .forEach((b) => b.classList.toggle("active", b.dataset.detail === detail));
  const node = state.nodes.find((n) => n.id === ref);
  $("detail-title").textContent =
    node?.title ||
    result.title ||
    (typeof ref === "string" ? ref : ref.local_id);
  renderInlineMath($("detail-title"));
  const box = $("detail-content");
  if (!cursor) box.replaceChildren();
  if (detail === "summary") {
    const staging=el("div");
    await populateNodeCard(staging, node || {...result,id:ref}, result, view);
    if(ref===state.selected&&view===state.view)box.replaceChildren(...staging.childNodes);
    $("detail-more").hidden=true;
    return;
  } else if (result.items) {
    if (detail === "lean" || detail === "nl")
      renderSourceRows(box, result.items);
    else if (detail === "sources") {
      for (const item of result.items) {
        box.append(el("div", "part-label", item.ref.local_id));
        for (const range of item.ranges || [])
          box.append(
            el(
              "p",
              "",
              `${range.asset_id} · lines ${range.start_line}–${range.end_line}`,
            ),
          );
        for (const origin of item.provenance || []) {
          const record = el("details", "edge-source");
          record.append(
            el("summary", "", origin.method),
            el("pre", "", origin.source_ref),
          );
          box.append(record);
        }
      }
    } else if (detail === "interfaces") {
      for (const item of result.items) {
        if (item.provider_decl && item.consumer_decl) {
          box.append(
            el("div", "part-label", item.relation_kind),
            button(
              `${item.provider_decl.local_id} → ${item.consumer_decl.local_id}`,
              guarded(async () => {
                const result = await api(
                  "locate",
                  args({ ref: item.consumer_decl }),
                );
                if (result.anchor) scrollToAnchor(result.anchor);
                state.selected = item.consumer_decl;
                await showDetail("summary");
              }),
            ),
          );
        } else addMember(box, item.ref || item);
      }
    } else result.items.forEach((item) => addMember(box, item));
  } else {
    box.append(
      el(
        "pre",
        "",
        result.text ??
          JSON.stringify(
            Object.fromEntries(
              Object.entries(result).filter(
                ([k]) => !["ok", "view_id"].includes(k),
              ),
            ),
            null,
            2,
          ),
      ),
    );
  }
  state.detailCursor = result.next_cursor;
  $("detail-more").hidden = !result.next_cursor;
}
async function recommendations() {
  const reader = state.reader,
    view = state.view;
  const result = await api("recommend", { reader_id: reader, limit: 3 });
  if (reader !== state.reader || view !== state.view) return;
  const box = $("recommendations");
  box.replaceChildren();
  if (result.view_id !== view) {
    state.latest = result.view_id;
    updateControls();
    box.append(
      el(
        "p",
        "empty",
        "A newer view is available. Return to latest to see suggestions.",
      ),
    );
    return;
  }
  for (const item of result.recommendations) {
    const node = state.nodes.find((n) => n.id === item.target_id);
    const b = button(
      "",
      guarded(() => act("expand", item.target_id)),
      "suggestion",
    );
    b.dataset.action = "expand";
    b.append(
      el("strong", "", node?.title || item.target_id), el("i", "suggestion-open", "↗"),
      el("span", "", result.policy_id === "random" ? tr("Random selection", "随机选择") : node?.description || tr("Explore the argument", "查看这部分论证")),
    );
    box.append(b);

  }
  box.querySelectorAll("strong, .suggestion span").forEach(renderInlineMath);
  if (!result.recommendations.length)
    box.append(
      el(
        "p",
        "empty",
        tr("All available sections are open. Explore their statements and proofs.", "当前所有章节均已展开，可阅读陈述与证明。"),
      ),
    );
}
async function act(action, target, extra = {}) {
  if (state.busy) return;
  setBusy(true);
  notice("");
  try {
    const response = await api("apply_action", {
      reader_id: state.reader,
      expected_view: state.view,
      action,
      target,
      ...extra,
    });
    if (response.job) {
      state.job = response.job.job_id;
      state.jobTarget = target;
      renderJob(response.job);
      if (generationStatus(response.job) && !generationTerminal(response.job)) {
        pollJob(response.job.job_id, state.reader);
        return;
      }
      state.job = null;
    } else {
      $("job").hidden = true;
      state.jobTarget = null;
    }
    state.latest = response.view_id;
    await loadView(response.view_id, {
      target: action === "switch_locale" ? (typeof state.selected === "string" ? `${state.selected}:section` : null) : response.changed_anchor || `${target}:section`,
    });
  } finally {
    setBusy(false);
  }
}
function generationStatus(job) {
  return job.status;
}
function generationTerminal(job) {
  return ["published", "failed", "cancelled"].includes(generationStatus(job));
}
function renderJob(job) {
  const box = $("job");
  const status = generationStatus(job);
  const completed = Math.max(0, Number(job.completed_children || 0));
  const total = Math.max(completed, Number(job.total_children || 0));
  const labels = {
    queued: tr("Waiting to prepare this section. Published text stays available.", "正在等待生成本节，已发布正文保持可读。"),
    drafting: tr(`Drafting children${total ? ` · ${completed}/${total}` : ""}.`, `正在生成子项${total ? ` · ${completed}/${total}` : ""}。`),
    stitching: tr("Joining the completed parts into a continuous passage.", "正在衔接已完成的子项。"),
    validating: tr("Checking the completed section before publication.", "正在发布前校验完整章节。"),
    published: tr("Section published.", "章节已发布。"),
    failed: job.error?.message || tr("Generation failed. Published text was not changed.", "生成失败，已发布正文没有改变。"),
    cancelled: tr("Generation cancelled. Published text was not changed.", "生成已取消，已发布正文没有改变。"),
  };
  box.hidden = false;
  box.className = "generation-placeholder";
  box.dataset.status = status;
  box.setAttribute("role", "status");
  box.setAttribute("aria-live", "polite");
  box.replaceChildren(el("span", "", labels[status] || tr(`Section ${status}.`, `章节状态：${status}。`)));
  if (total && ["drafting", "stitching", "validating"].includes(status)) {
    const progress = el("span", "generation-progress");
    progress.setAttribute("aria-label", `${completed} / ${total}`);
    for (let index = 0; index < total; index++) progress.append(el("i", index < completed ? "done" : ""));
    box.append(progress);
  }
  if (["queued", "drafting", "stitching", "validating"].includes(status))
    box.append(
      button(
        "Cancel",
        guarded(async () => {
          const r = await api("apply_action", {
            reader_id: state.reader,
            expected_view: state.view,
            action: "cancel",
            target: job.job_id,
          });
          renderJob(r.job);
        }),
      ),
    );
  if (["failed", "cancelled"].includes(status) && state.jobTarget)
    box.append(button(tr("Retry", "重试"), guarded(() => act("expand", state.jobTarget))));
  const target = state.jobTarget && document.getElementById(`${state.jobTarget}:section`);
  if (target && box.parentElement !== target) target.insertBefore(box, target.querySelector(".section-body") || null);
}
async function pollJob(jobId, reader) {
  try {
    while (state.job === jobId && state.reader === reader) {
      await new Promise((resolve) => setTimeout(resolve, 700));
      const response = await api("inspect", {
        reader_id: reader,
        ref: jobId,
        detail: "job",
      });
      const job = response.job;
      renderJob(job);
      if (generationTerminal(job)) {
        state.job = null;
        state.latest = job.latest_view;
        updateControls();
        if (generationStatus(job) === "published" && job.applied) {
          $("job").hidden = true;
          await loadView(job.result_view, { target: job.changed_anchor });
        } else if (generationStatus(job) === "published") {
          notice(
            "Section prepared. The reading view changed during generation; return to latest and open the section again.",
            false,
            job.latest_view,
          );
        } else if (generationStatus(job) === "failed") {
          notice(
            job.error?.message ||
              "Generation failed. Open the section again to retry.",
            true,
          );
        }
        return;
      }
    }
  } catch (error) {
    state.job = null;
    updateControls();
    notice(error.message, true, error.latest_view);
  }
}
async function open(instance) {
  ++state.epoch;
  graphCamera.initialized = false;
  graphCamera.positions.clear();
  graphCamera.pinned.clear();
  state.job = null;
  $("job").hidden = true;
  state.selected = null;
  state.history = [];
  $("detail-content").replaceChildren();
  $("detail-tabs").hidden = true;
  setBusy(true);
  try {
    const result = await api("open_reader", {
      instance_id: instance.instance_id,
    });
    state.reader = result.reader_id;
    state.view = result.view_id;
    state.latest = result.view_id;
    state.root = result.root_id;
    $("title").textContent = instance.title;
    const count = instance.declaration_count;
    $("scope-label").hidden = count === undefined;
    $("scope-label").textContent = count !== undefined ? `${count} declarations` : "";
    await loadView(result.view_id);
  } finally {
    setBusy(false);
  }
}
$("budget-form").onsubmit = guarded(async (event) => {
  event.preventDefault();
  const raw = $("budget").value;
  const value = raw === "" ? null : Number(raw);
  if (value !== null && (!Number.isSafeInteger(value) || value < 0))
    throw new Error(
      "Enter a nonnegative whole number, or leave the budget empty.",
    );
  await act("set_budget", state.root, { budget_codepoints: value });
});
$("back").onclick = guarded(async () => {
  state.history.pop();
  await loadView(state.history.at(-1), { remember: false });
});
$("latest").onclick = guarded(() => loadView(state.latest));
$("reset").onclick = guarded(() => act("reset", state.root));
$("detail-more").onclick = guarded(() =>
  state.detail === "edge"
    ? inspectEdge(state.selected, state.detailCursor)
    : showDetail(state.detail, state.detailCursor),
);
$("clear-detail").onclick = () => {
  state.selected = null;
  $("detail-content").replaceChildren();
  $("detail-tabs").hidden = true;
  $("detail-title").textContent =
    "Select text or a map node to inspect its sources and connections.";
};
$("detail-tabs")
  .querySelectorAll("button")
  .forEach((b) => (b.onclick = guarded(() => showDetail(b.dataset.detail))));
guarded(async () => {
  const response = await fetch("/api/instances");
  if (!response.ok)
    throw new Error("The document library could not be loaded.");
  const { instances } = await response.json();
  instances.sort((a,b) => (a.locale === "zh" ? -1 : 0) - (b.locale === "zh" ? -1 : 0));
  state.instances = instances;
  if (!instances.length) {
    $("title").textContent = "Your library is empty";
    notice("Load a fixed content instance to start reading.");
    setBusy(false);
    return;
  }
  const groups = new Map();
  for (const instance of instances) {
    if (!groups.has(instance.structure_id)) {
      const group = el("optgroup"); group.label = instance.title;
      groups.set(instance.structure_id, group); $("instances").append(group);
    }
    const option = el("option", "", `${instance.title} · ${instance.locale || tr("default", "默认")}`);
    option.value = instance.instance_id;
    groups.get(instance.structure_id).append(option);
  }
  $("instances").onchange = guarded(() =>
    open(instances.find((i) => i.instance_id === $("instances").value)),
  );
  await open(instances[0]);
})();

const tr = (en, zh) => state.locale === "zh" ? zh : en;
function localize() {
  const values = {
    "hdg-label": ["DEPENDENCY GRAPH", "依赖图"], "graph-fit": ["Fit all", "查看全图"],
    "graph-reset": ["Reset layout", "重置布局"], "map-pin": ["Keep open", "保持展开"],
    "hdg-help": ["Arrows run from provider to consumer. Drag nodes to fix them; drag empty space to pan.", "箭头从依赖提供者指向使用者。拖动节点固定位置，拖动空白处平移。"],
    "reset": ["Collapse all", "全部折叠"], "back": ["← Previous view", "← 上一视图"],
    "latest": ["Return to latest", "返回最新视图"], "budget-save": ["Set", "设置"],
    "detail-more": ["Load more", "加载更多"],
  };
  for (const [id, pair] of Object.entries(values)) $(id).textContent = tr(...pair);
  const staticLabels = {
    ".collection": ["MATHEMATICS / READER", "数学 / 阅读器"], ".local-badge": ["LOCAL LIBRARY", "本地文库"],
    ".map-panel .panel-heading > span:first-child": ["DOCUMENT OUTLINE", "章节目录"],
    ".map-panel > .panel-help": ["Select a chapter to locate its text. Expand explicitly in the text or graph.", "选择章节定位正文，在正文或图中点击展开按钮。"],
    ".document-heading > .eyebrow": ["AN EXPANDABLE EXPOSITION", "可逐级展开的数学讲解"],
    ".deck": ["Read the argument at your own pace. Open a section to explore the details.", "按自己的节奏阅读论证，展开章节查看细节。"],
    ".suggestions > .eyebrow": ["EXPLORE NEXT", "继续探索"], ".suggestions > h2": ["Explore the proof", "深入论证"],
    ".suggestions > .panel-help": ["Expansion suggestions explain their structural ranking; they do not estimate understanding.", "展开建议说明结构排序依据，不估计理解收益。"],
    ".details-heading > h2": ["Selected passage", "选中内容"],
    ".document-footer > span:first-child": ["END OF CURRENT VIEW", "当前视图结束"],
    ".keyboard-hint": ["Tab moves between controls. Enter selects. Explicit + / − expands or collapses. Hover only highlights.", "Tab 切换控件，Enter 选择；点击 + / − 展开或折叠。悬停仅高亮。"],
    ".map-legend": ["Outline and graph follow the current reading scope.", "目录与依赖图同步当前阅读范围。"],
  };
  for (const [selector, pair] of Object.entries(staticLabels)) {
    const element = document.querySelector(selector); if (element) element.textContent = tr(...pair);
  }
  for (const [selector, pair] of Object.entries({".instance-label":["Document", "文档"],".locale-label":["Language", "语言"],"#budget-form label":["Display budget ","显示预算 "]})) {
    const element = document.querySelector(selector);
    if (element?.firstChild?.nodeType === Node.TEXT_NODE) element.firstChild.textContent = tr(...pair);
  }
  $("budget").placeholder = tr("Unlimited", "不限");
  document.querySelectorAll(".hdg-legend span").forEach((e,i)=>{e.lastChild.textContent=i===2?tr(" edge: evidence count"," 线宽：依赖证据数"):i?tr(" shade: dependencies"," 深浅：依赖量"):tr(" size: text amount"," 大小：正文量");});
  const tabs = {summary:["Overview","概要"],interfaces:["Links","关系"],members:["Members","成员"],nl:["Original","原文"],lean:["Lean","Lean"],sources:["Sources","来源"]};
  $("detail-tabs").querySelectorAll("button").forEach(b => b.textContent=tr(...tabs[b.dataset.detail]));
}
$("locale").onchange = guarded(async () => {
  const locale = $("locale").value;
  try { await act("switch_locale", state.root, {locale}); }
  finally { $("locale").value = state.locale; }
});

const mapPanel = document.querySelector(".map-panel");
let mapCloseTimer = null;
function setMapOpen(open) {
  if (mapPanel.classList.contains("pinned") && !open) return;
  mapPanel.classList.toggle("open", open);
  $("map-handle").setAttribute("aria-expanded", String(open || mapPanel.classList.contains("pinned")));
}
mapPanel.addEventListener("mouseenter", () => { clearTimeout(mapCloseTimer); setMapOpen(true); });
mapPanel.addEventListener("mouseleave", () => { clearTimeout(mapCloseTimer); mapCloseTimer = setTimeout(() => setMapOpen(false), 220); });
$("map-handle").onclick = () => setMapOpen(!mapPanel.classList.contains("open"));
$("map-pin").onclick = () => {
  const pinned = mapPanel.classList.toggle("pinned");
  $("map-pin").setAttribute("aria-pressed", String(pinned));
  $("map-pin").textContent = tr(pinned ? "Release" : "Keep open", pinned ? "取消固定" : "保持展开");
  setMapOpen(pinned);
};
$("map-close").onclick = () => {
  mapPanel.classList.remove("pinned", "open");
  $("map-pin").setAttribute("aria-pressed", "false");
  $("map-handle").setAttribute("aria-expanded", "false");
  $("map-handle").focus();
};
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !mapPanel.classList.contains("pinned")) setMapOpen(false);
});

// The network contains frontier nodes and external interfaces, never chapter containers.
const graphCamera = {x:12,y:12,scale:1,initialized:false,positions:new Map(),pinned:new Set(),nodes:[],edges:[],bounds:null};
function svgElement(tag, attributes = {}, text = null) {
  const element = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [key,value] of Object.entries(attributes)) element.setAttribute(key, String(value));
  if (text !== null) element.textContent = text;
  return element;
}
function transformGraph() {
  $("hdg-world")?.setAttribute("transform", `translate(${graphCamera.x},${graphCamera.y}) scale(${graphCamera.scale})`);
}
function fitGraph() {
  const bounds = graphCamera.bounds;
  if (!bounds) return;
  graphCamera.scale = Math.min(1, ($("hdg").clientWidth-30)/bounds.width, ($("hdg").clientHeight-30)/bounds.height);
  graphCamera.x = ($("hdg").clientWidth-bounds.width*graphCamera.scale)/2-bounds.minX*graphCamera.scale;
  graphCamera.y = ($("hdg").clientHeight-bounds.height*graphCamera.scale)/2-bounds.minY*graphCamera.scale;
  transformGraph();
}
function visibleCodepoints(node) {
  const parts = node.kind === "unit" ? ["statement","proof","content"] : ["lead_in","synopsis","lead_out"];
  return parts.reduce((sum, part) => {
    const anchor = state.anchors.find((item) => item.anchor_id === `${node.id}:${part}`);
    if (!anchor || anchor.end_line < anchor.start_line) return sum;
    return sum + [...state.lines.slice(anchor.start_line-1, anchor.end_line).join("\n").trim()].length;
  }, 0);
}
function dependencyBurden(id, edges) {
  return edges.reduce((sum, edge) => sum + ((edge.provider_node === id || edge.consumer_node === id) ? Number(edge.evidence_count || 0) : 0), 0);
}
function dependencyColor(burden, external) {
  if (external) return "#d8cec0";
  const t = Math.min(1, Math.log2(1 + Math.max(0, burden)) / 8);
  const light=[210,231,218], dark=[35,91,65];
  return `rgb(${light.map((v,i)=>Math.round(v+(dark[i]-v)*t)).join(",")})`;
}
function contentRadius(codepoints) {
  // Fixed across views: area grows with log content, without early saturation.
  return Math.min(28, Math.sqrt(25 + 52 * Math.log2(1 + Math.max(0, codepoints) / 80)));
}
function edgeWidth(count) { return Math.min(4.5, .85 + .6 * Math.log2(Math.max(1, count))); }
function graphRanks(nodes, edges) {
  const incoming=new Map(nodes.map(n=>[n.id,0])), ranks=new Map(nodes.map(n=>[n.id,0])), outgoing=new Map(nodes.map(n=>[n.id,[]]));
  for(const edge of edges){incoming.set(edge.consumer_node,incoming.get(edge.consumer_node)+1);outgoing.get(edge.provider_node).push(edge.consumer_node);}
  const queue=nodes.filter(n=>!incoming.get(n.id)).map(n=>n.id).sort(),visited=new Set();
  while(queue.length){const id=queue.shift();visited.add(id);for(const child of outgoing.get(id).sort()){ranks.set(child,Math.max(ranks.get(child),ranks.get(id)+1));incoming.set(child,incoming.get(child)-1);if(!incoming.get(child)){queue.push(child);queue.sort();}}}
  const cycleRank=Math.max(0,...[...visited].map(id=>ranks.get(id)+1));
  for(const node of nodes) if(!visited.has(node.id)) ranks.set(node.id,cycleRank);
  return ranks;
}
function layoutGraph(nodes, edges) {
  const old=graphCamera.positions, ranks=graphRanks(nodes,edges), layers=new Map();
  for(const node of [...nodes].sort((a,b)=>(a.order??0)-(b.order??0)||a.id.localeCompare(b.id))){const rank=ranks.get(node.id)||0;if(!layers.has(rank))layers.set(rank,[]);layers.get(rank).push(node);}
  const positions=new Map();
  for(const [rank,layer] of layers) layer.forEach((node,index)=>{
    const codepoints=visibleCodepoints(node), burden=dependencyBurden(node.id,edges), r=contentRadius(codepoints);
    const prior=old.get(node.id), parent=old.get(node.parent);
    positions.set(node.id,{x:prior?.x??parent?.x??rank*72,y:prior?.y??((parent?.y??0)+Math.sin((rank+index+1)*2.4)*95+index*65),r,codepoints,burden,rank});
  });
  const list=[...nodes].sort((a,b)=>a.id.localeCompare(b.id));
  const degrees=new Map(nodes.map(n=>[n.id,0]));
  for(const edge of edges){degrees.set(edge.provider_node,degrees.get(edge.provider_node)+1);degrees.set(edge.consumer_node,degrees.get(edge.consumer_node)+1);}
  for(let iteration=0;iteration<180;iteration++){
    const force=new Map(list.map(node=>[node.id,{x:0,y:0}]));
    for(let i=0;i<list.length;i++)for(let j=i+1;j<list.length;j++){
      const a=positions.get(list[i].id),b=positions.get(list[j].id);let dx=b.x-a.x,dy=b.y-a.y,d=Math.hypot(dx,dy)||.01;
      const desired=a.r+b.r+90, strength=d<desired?(desired-d)*.10:Math.min(9000/(d*d),3);
      dx/=d;dy/=d;force.get(list[i].id).x-=dx*strength;force.get(list[i].id).y-=dy*strength;force.get(list[j].id).x+=dx*strength;force.get(list[j].id).y+=dy*strength;
    }
    for(const edge of edges){const a=positions.get(edge.provider_node),b=positions.get(edge.consumer_node);if(!a||!b)continue;const dx=b.x-a.x,dy=b.y-a.y,d=Math.hypot(dx,dy)||1,pull=(d-180)*.012/Math.sqrt(Math.max(degrees.get(edge.provider_node),degrees.get(edge.consumer_node),1));force.get(edge.provider_node).x+=dx/d*pull;force.get(edge.provider_node).y+=dy/d*pull;force.get(edge.consumer_node).x-=dx/d*pull;force.get(edge.consumer_node).y-=dy/d*pull;}
    for(const node of list){if(graphCamera.pinned.has(node.id))continue;const p=positions.get(node.id),f=force.get(node.id);f.x+=((p.rank||0)*72-p.x)*.004;f.y+=-p.y*.0008;p.x+=Math.max(-5,Math.min(5,f.x));p.y+=Math.max(-5,Math.min(5,f.y));}
  }
  // A final deterministic collision pass gives the visible circles hard separation.
  for(let pass=0;pass<24;pass++)for(let i=0;i<list.length;i++)for(let j=i+1;j<list.length;j++){
    const a=positions.get(list[i].id),b=positions.get(list[j].id);let dx=b.x-a.x,dy=b.y-a.y,d=Math.hypot(dx,dy)||.01,min=a.r+b.r+8;if(d>=min)continue;const push=(min-d)/2,ux=dx/d,uy=dy/d;
    if(!graphCamera.pinned.has(list[i].id)){a.x-=ux*push;a.y-=uy*push;}if(!graphCamera.pinned.has(list[j].id)){b.x+=ux*push;b.y+=uy*push;}
  }
  return positions;
}
function bezierPoint(route,t){const s=1-t;return{x:s*s*s*route.x1+3*s*s*t*route.c1x+3*s*t*t*route.c2x+t*t*t*route.x2,y:s*s*s*route.y1+3*s*s*t*route.c1y+3*s*t*t*route.c2y+t*t*t*route.y2};}
function routeEdge(edge, chosen) {
  const a=graphCamera.positions.get(edge.provider_node),b=graphCamera.positions.get(edge.consumer_node);let dx=b.x-a.x,dy=b.y-a.y,d=Math.hypot(dx,dy)||1,ux=dx/d,uy=dy/d,nx=-uy,ny=ux;
  let best=null;
  for(const offset of [0,24,-24,48,-48,76,-76,104,-104]){
    const route={x1:a.x+ux*(a.r+2),y1:a.y+uy*(a.r+2),x2:b.x-ux*(b.r+5),y2:b.y-uy*(b.r+5)};
    route.c1x=route.x1+dx*.34+nx*offset;route.c1y=route.y1+dy*.34+ny*offset;route.c2x=route.x1+dx*.66+nx*offset;route.c2y=route.y1+dy*.66+ny*offset;
    route.samples=Array.from({length:19},(_,i)=>bezierPoint(route,(i+1)/20));let penalty=Math.abs(offset)*.15+d;
    for(const node of graphCamera.nodes){if(node.id===edge.provider_node||node.id===edge.consumer_node)continue;const p=graphCamera.positions.get(node.id);for(const point of route.samples)if(Math.hypot(point.x-p.x,point.y-p.y)<p.r+7)penalty+=400;}
    for(const previous of chosen)for(const point of route.samples)if(previous.some(other=>Math.hypot(point.x-other.x,point.y-other.y)<5))penalty+=2;
    if(!best||penalty<best.penalty)best={...route,penalty};
  }
  return best;
}
function graphBounds() {
  const positions=[...graphCamera.positions.values()];if(!positions.length)return{minX:0,minY:0,width:1,height:1};
  const minX=Math.min(...positions.map(p=>p.x-p.r-35)),maxX=Math.max(...positions.map(p=>p.x+p.r+35)),minY=Math.min(...positions.map(p=>p.y-p.r-15)),maxY=Math.max(...positions.map(p=>p.y+p.r+35));
  return{minX,minY,width:Math.max(1,maxX-minX),height:Math.max(1,maxY-minY)};
}
function drawHDG() {
  const svg=$("hdg");svg.replaceChildren();
  const defs=svgElement("defs"),marker=svgElement("marker",{id:"hdg-arrow",viewBox:"0 0 10 10",refX:9,refY:5,markerWidth:7,markerHeight:7,markerUnits:"userSpaceOnUse",orient:"auto-start-reverse"});marker.append(svgElement("path",{d:"M2 2L8 5L2 8",fill:"none",stroke:"#709482","stroke-width":1.3}));defs.append(marker);svg.append(defs);
  const world=svgElement("g",{id:"hdg-world"}),edgeLayer=svgElement("g"),nodeLayer=svgElement("g");world.append(edgeLayer,nodeLayer);svg.append(world);
  const chosen=[];
  for(const edge of [...graphCamera.edges].sort((a,b)=>a.id.localeCompare(b.id))){const route=routeEdge(edge,chosen);chosen.push(route.samples);const path=svgElement("path",{d:`M${route.x1},${route.y1} C${route.c1x},${route.c1y} ${route.c2x},${route.c2y} ${route.x2},${route.y2}`,class:`hdg-edge ${state.selected===edge.id?"selected":""} ${(state.selected===edge.provider_node||state.selected===edge.consumer_node)?"incident":""}`,"marker-end":"url(#hdg-arrow)","data-edge":edge.id,"data-provider":edge.provider_node,"data-consumer":edge.consumer_node,tabindex:0,role:"button","aria-label":tr(`${edge.evidence_count} dependency evidence records`,`${edge.evidence_count} 条依赖证据`)});path.style.setProperty("--edge-width",`${edgeWidth(edge.evidence_count)}px`);path.onclick=guarded(()=>inspectEdge(edge.id));path.onkeydown=event=>{if(event.key==="Enter"||event.key===" "){event.preventDefault();path.onclick();}};path.onmouseenter=()=>{highlight(edge.provider_node,true);highlight(edge.consumer_node,true)};path.onmouseleave=()=>{highlight(edge.provider_node,false);highlight(edge.consumer_node,false)};edgeLayer.append(path);}
  for(const node of graphCamera.nodes){const p=graphCamera.positions.get(node.id),external=node.kind==="external",neighbors=new Set();let incoming=0,outgoing=0;for(const edge of graphCamera.edges){if(edge.provider_node===node.id){outgoing++;neighbors.add(edge.consumer_node)}if(edge.consumer_node===node.id){incoming++;neighbors.add(edge.provider_node)}}
    const group=svgElement("g",{"data-node":node.id,class:`hdg-node ${external?"external":""} ${state.selected===node.id?"selected":""} ${graphCamera.pinned.has(node.id)?"pinned":""}`,tabindex:0,role:"button","aria-label":`${node.title}. ${p.codepoints} ${tr("text codepoints","正文码点")}; ${neighbors.size} ${tr("neighbors","相邻节点")}; ${incoming} ${tr("incoming","入边")}; ${outgoing} ${tr("outgoing","出边")}; ${p.burden} ${tr("evidence records","依赖证据")}.`});
    group.append(svgElement("circle",{class:"visible-dot",cx:p.x,cy:p.y,r:p.r,fill:dependencyColor(p.burden,external)}),svgElement("circle",{class:"hit-dot",cx:p.x,cy:p.y,r:Math.max(18,p.r)}));

    group.onmouseenter=()=>{highlight(node.id,true);scheduleGraphCard(node,group);};
    group.onmouseleave=()=>{highlight(node.id,false);scheduleCardClose();};
    group.onclick=guarded(async()=>{if(!graphMoved){await select(node.id,false);await openGraphCard(node,group,true);}});
    group.ondblclick=event=>{event.stopPropagation();graphCamera.pinned.delete(node.id);renderHDG();};
    group.onkeydown=event=>{if(event.key==="Enter"||event.key===" "){event.preventDefault();group.onclick();}};
    nodeLayer.append(group);
  }
  graphCamera.bounds=graphBounds();transformGraph();
}
function renderHDG() {
  const nodes = state.nodes.filter(n => n.is_frontier || n.kind === "external");
  const ids = new Set(nodes.map(n => n.id));
  const dependencies = (state.edges || []).filter(e => ids.has(e.provider_node) && ids.has(e.consumer_node));
  const membershipChanged=nodes.length!==graphCamera.nodes.length||nodes.some(n=>!graphCamera.positions.has(n.id));
  graphCamera.nodes=nodes;graphCamera.edges=dependencies;graphCamera.positions=layoutGraph(nodes,dependencies);drawHDG();
  const bounds=graphCamera.bounds,scale=graphCamera.scale;
  const outside=graphCamera.x+bounds.minX*scale<0||graphCamera.y+bounds.minY*scale<0||graphCamera.x+(bounds.minX+bounds.width)*scale>$("hdg").clientWidth||graphCamera.y+(bounds.minY+bounds.height)*scale>$("hdg").clientHeight;
  if(!graphCamera.initialized||(membershipChanged&&outside)){fitGraph();graphCamera.initialized=true;}
}
$("graph-fit").onclick=fitGraph;
$("graph-reset").onclick=()=>{graphCamera.pinned.clear();graphCamera.positions.clear();graphCamera.initialized=false;renderHDG();};
new ResizeObserver(() => { if (graphCamera.initialized) fitGraph(); }).observe($("hdg"));
$("graph-plus").onclick=()=>{graphCamera.scale=Math.min(3,graphCamera.scale*1.2);transformGraph();};
$("graph-minus").onclick=()=>{graphCamera.scale=Math.max(.08,graphCamera.scale/1.2);transformGraph();};
$("hdg").addEventListener("wheel",event=>{
  event.preventDefault();const rect=$("hdg").getBoundingClientRect(),x=event.clientX-rect.left,y=event.clientY-rect.top;
  const old=graphCamera.scale;graphCamera.scale=Math.max(.08,Math.min(3,old*Math.exp(-event.deltaY*.001)));
  graphCamera.x=x-(x-graphCamera.x)*graphCamera.scale/old;graphCamera.y=y-(y-graphCamera.y)*graphCamera.scale/old;transformGraph();
},{passive:false});
let graphDrag=null, graphMoved=false;
$("hdg").onpointerdown=event=>{
  if(event.button!==0)return;
  graphMoved=false;
  const node=event.target.closest?.(".hdg-node");
  graphDrag={x:event.clientX,y:event.clientY,startX:event.clientX,startY:event.clientY,node:node?.dataset.node||null};
};
$("hdg").onpointermove=event=>{
  if(!(event.buttons & 1)){graphDrag=null;return;}
  if(!graphDrag)return;
  if(!graphMoved && Math.hypot(event.clientX-graphDrag.startX,event.clientY-graphDrag.startY)<4)return;
  graphMoved=true;
  $("hdg").setPointerCapture(event.pointerId);
  if(graphDrag.node){const p=graphCamera.positions.get(graphDrag.node);p.x+=(event.clientX-graphDrag.x)/graphCamera.scale;p.y+=(event.clientY-graphDrag.y)/graphCamera.scale;graphCamera.pinned.add(graphDrag.node);drawHDG();$("hdg").querySelector(`[data-node="${CSS.escape(graphDrag.node)}"]`)?.classList.add("dragging");}
  else{graphCamera.x+=event.clientX-graphDrag.x;graphCamera.y+=event.clientY-graphDrag.y;transformGraph();}
  graphDrag.x=event.clientX;graphDrag.y=event.clientY;transformGraph();
};
$("hdg").onpointerup=()=>{graphDrag=null;};
$("hdg").onpointercancel=()=>{graphDrag=null;graphMoved=false;};
$("hdg").addEventListener("click",event=>{
  if(graphMoved){event.stopImmediatePropagation();event.preventDefault();graphMoved=false;}
},true);

function partLabel(node, part) {
  if (part === "statement") return tr("Statement", "定理陈述");
  if (part === "proof") return tr("Proof", "证明");
  const kinds = {def:["Definition","定义"],definition:["Definition","定义"],abbrev:["Abbreviation","缩写定义"],structure:["Structure","结构"],inductive:["Inductive definition","归纳定义"],instance:["Instance","实例"],axiom:["Axiom","公理"],opaque:["Opaque definition","不透明定义"],constructor:["Constructor","构造子"],recursor:["Recursor","递归原理"],compiler_only:["Technical declaration","技术声明"]};
  return tr(...(kinds[node.raw_kind] || ["Declaration", "声明"]));
}

function kindLabel(kind) {
  const values = {repo:["Repository","项目"],scope:["Chapter","章节"],region:["Region","分组"],unit:["Declaration","声明"],theorem:["Theorem","定理"],lemma:["Lemma","引理"],def:["Definition","定义"],definition:["Definition","定义"],abbrev:["Abbreviation","缩写定义"],structure:["Structure","结构"],inductive:["Inductive definition","归纳定义"],external:["External interface","外部接口"],constructor:["Constructor","构造子"],recursor:["Recursor","递归原理"],instance:["Instance","实例"],axiom:["Axiom","公理"],opaque:["Opaque definition","不透明定义"],compiler_only:["Technical declaration","技术声明"]};
  return tr(...(values[kind] || [kind, kind]));
}

function focusGraph(id) {
  const box = graphCamera.positions.get(id);
  if (!box) return;
  if (graphCamera.scale < .45) graphCamera.scale = .8;
  const diameter = box.r * 2;
  const left = (box.x - box.r) * graphCamera.scale + graphCamera.x;
  const top = (box.y - box.r) * graphCamera.scale + graphCamera.y;
  if (left < 0 || left + diameter * graphCamera.scale > $("hdg").clientWidth || top < 0 || top + diameter * graphCamera.scale > $("hdg").clientHeight) {
    graphCamera.x = $("hdg").clientWidth / 2 - box.x * graphCamera.scale;
    graphCamera.y = $("hdg").clientHeight / 2 - box.y * graphCamera.scale;
  }
  transformGraph();
}

function renderInlineMath(element) {
  renderMathInElement(element, {
    delimiters: [
      {left:"$$",right:"$$",display:false},
      {left:"$",right:"$",display:false},
      {left:"\\(",right:"\\)",display:false},
      {left:"\\[",right:"\\]",display:false},
    ],
    ignoredClasses:["katex"], throwOnError:false, trust:false,
  });
}

// Cards use the same immutable view as the text; opening one never expands it.
let cardTimer, cardCloseTimer, cardSequence = 0, cardPinned = false;
const graphCard = el("aside", "graph-card");
graphCard.hidden = true;
graphCard.setAttribute("aria-label", "Node details");
document.body.append(graphCard);
graphCard.onmouseenter = () => clearTimeout(cardCloseTimer);
graphCard.onmouseleave = () => scheduleCardClose();
function closeGraphCard() { cardSequence++; graphCard.hidden=true; cardPinned=false; clearTimeout(cardTimer); }
function scheduleCardClose() { clearTimeout(cardTimer); clearTimeout(cardCloseTimer); if(!cardPinned) cardCloseTimer=setTimeout(closeGraphCard,250); }
function scheduleGraphCard(node, target) { clearTimeout(cardTimer); clearTimeout(cardCloseTimer); if(!cardPinned) cardTimer=setTimeout(guarded(()=>openGraphCard(node,target,false)),350); }
document.addEventListener("keydown",event=>{if(event.key==="Escape")closeGraphCard();});
document.addEventListener("pointerdown",event=>{if(!graphCard.contains(event.target)&&!event.target.closest('.hdg-node'))closeGraphCard();});
async function inspectAll(ref, detail, view) {
  const items=[];let cursor;
  do { const page=await api("inspect",{reader_id:state.reader,view_id:view,ref,detail,limit:200,...(cursor?{cursor}:{})}); items.push(...(page.items||[]));cursor=page.next_cursor; } while(cursor);
  return items;
}
function cardSection(box, title, text, code=false) {
  if(!text?.trim())return;
  const section=el("section","node-card-section");section.append(el("h3","",title));
  const content=el(code?"pre":"div",code?"":"prose");
  if(code){const c=el("code","language-lean",text);content.append(c);}else markdown(text,content);
  section.append(content);box.append(section);
}
function sourceGroups(items) {
  const grouped=new Map();
  for(const item of items){if(item.status!=="present"||item.text==null)continue;const key=JSON.stringify(item.ref);if(!grouped.has(key))grouped.set(key,{ref:item.ref,statement:[],proof:[]});grouped.get(key)[item.part]?.push(item.text);}
  return [...grouped.values()].map(g=>({...g,statement:g.statement.join("\n"),proof:g.proof.join("\n")}));
}
async function openGraphCard(node,target,pinned) {
  clearTimeout(cardTimer);clearTimeout(cardCloseTimer);const sequence=++cardSequence,view=state.view;cardPinned=pinned;
  const header=el("header","graph-card-header");
  const title=el("h2","",node.title);title.id="graph-card-title";
  const close=button("×",closeGraphCard,"graph-card-close");close.setAttribute("aria-label",tr("Close details","关闭详情"));
  header.append(title,el("div","card-kind",kindLabel(node.raw_kind||node.kind)),close);
  graphCard.setAttribute("aria-labelledby","graph-card-title");
  graphCard.replaceChildren(header);
  const content=el("div","graph-card-body");graphCard.append(content);graphCard.hidden=false;
  const rect=target.getBoundingClientRect(),width=Math.min(370,innerWidth-24);
  graphCard.style.left=`${Math.max(12,Math.min(innerWidth-width-12,rect.left-width-16))}px`;
  graphCard.style.top=`${Math.max(12,Math.min(innerHeight-340,rect.top-45))}px`;
  content.append(el("p","card-empty",tr("Loading…","正在读取…")));
  const result=await api("inspect",{reader_id:state.reader,view_id:view,ref:node.id,detail:"summary"});
  const staging=el("div");await populateNodeCard(staging,node,result,view);
  if(sequence!==cardSequence||view!==state.view)return;
  content.replaceChildren(...staging.childNodes);renderInlineMath(graphCard.querySelector('h2'));
}
async function populateNodeCard(box,node,summary,view) {
  const ref=node.id;
  const staging=el("div");
  if(summary.description) cardSection(staging,tr("Summary","概要"),summary.description);
  if(node.kind!=="unit"&&node.kind!=="external") {
    await regionPreview(staging,node,view);
    const anchor=state.anchors.find(a=>a.anchor_id===`${ref}:synopsis`);
    if(anchor&&!summary.description)cardSection(staging,tr("Argument","论证"),state.lines.slice(anchor.start_line-1,anchor.end_line).join("\n"));
  } else {
    const [nl,lean]=await Promise.all([inspectAll(ref,"nl",view),inspectAll(ref,"lean",view)]);
    const natural=sourceGroups(nl),formal=sourceGroups(lean);
    // Representative declaration is first in the source contract; helpers retain separate code blocks.
    const anchor=state.anchors.find(a=>[`${ref}:statement`,`${ref}:content`].includes(a.anchor_id));
    const statement=anchor?state.lines.slice(anchor.start_line-1,anchor.end_line).join("\n"):natural[0]?.statement;
    cardSection(staging,tr("Statement","数学陈述"),statement);
    for(const group of formal){
      const complete=group.statement.includes(":=")?group.statement:group.proof?`${group.statement}\n:= ${group.proof}`:group.statement;
      cardSection(staging,formal.length>1?group.ref.local_id:tr("Lean source","Lean 源码"),complete,true);
    }
    if(!natural.length&&!formal.length)staging.append(el("p","card-empty",tr("No source text is available for this interface.","此接口暂无可用源文。")));
  }
  if(node.can_expand||node.can_collapse){const action=node.can_expand?"expand":"collapse";const b=button(tr(node.can_expand?"Explore this section  ›":"Collapse this section  ‹",node.can_expand?"展开这部分论证  ›":"收起这部分论证  ‹"),guarded(async()=>{closeGraphCard();await act(action,ref);}),"card-action");b.dataset.action=action;b.dataset.target=ref;b.disabled=state.busy||state.view!==state.latest||!!state.job;staging.append(b);}
  if(view===state.view)box.replaceChildren(...staging.childNodes);
}
async function regionPreview(box,node,view) {
  const [relations,members]=await Promise.all([inspectAll(node.id,"interfaces",view),inspectAll(node.id,"members",view)]);
  const refs=members.slice(0,32),locations=await Promise.all(refs.map(ref=>api("locate",{reader_id:state.reader,view_id:view,ref})));
  const unitToChild=new Map(),children=new Set();
  for(const location of locations){const path=location.expand_path||[],index=path.indexOf(node.id);let child=index>=0?(path[index+1]||location.node_id):location.node_id;
    let visible=state.nodes.find(n=>n.id===child);while(visible?.parent&&visible.parent!==node.id){child=visible.parent;visible=state.nodes.find(n=>n.id===child);}
    unitToChild.set(location.node_id,child);children.add(child);
  }
  const referenceLocations=new Map(refs.map((ref,i)=>[JSON.stringify(ref),locations[i]]));
  const boundaryRefs=new Map();
  for(const rel of relations)for(const ref of [rel.provider_decl,rel.consumer_decl])if(ref&&!referenceLocations.has(JSON.stringify(ref)))boundaryRefs.set(JSON.stringify(ref),ref);
  await Promise.all([...boundaryRefs].map(async([key,ref])=>referenceLocations.set(key,await api("locate",{reader_id:state.reader,view_id:view,ref}))));
  for(const rel of relations){
    const provider=referenceLocations.get(JSON.stringify(rel.provider_decl)),consumer=referenceLocations.get(JSON.stringify(rel.consumer_decl));
    if(provider)rel.provider_node=unitToChild.has(provider.node_id)?provider.node_id:provider.visible_ancestor||provider.node_id;
    if(consumer)rel.consumer_node=unitToChild.has(consumer.node_id)?consumer.node_id:consumer.visible_ancestor||consumer.node_id;
  }
  const endpoints=new Map([...children].map(id=>[id,{id,column:1}])),edges=[];
  for(const rel of relations){if(!rel.provider_node||!rel.consumer_node)continue;
    const provider=unitToChild.get(rel.provider_node)||rel.provider_node,consumer=unitToChild.get(rel.consumer_node)||rel.consumer_node;if(provider===consumer)continue;
    if(!endpoints.has(provider))endpoints.set(provider,{id:provider,column:0});if(!endpoints.has(consumer))endpoints.set(consumer,{id:consumer,column:2});edges.push([provider,consumer]);
  }
  const points=[...endpoints.values()].slice(0,36);
  await Promise.all(points.map(async p=>{const visible=state.nodes.find(n=>n.id===p.id);if(visible){p.title=visible.title;return;}const info=await api("inspect",{reader_id:state.reader,view_id:view,ref:p.id,detail:"summary"});p.title=info.title;}));
  const section=el("section","node-card-section"),svg=svgElement("svg",{class:"interface-map",role:"img","aria-label":tr("Inputs, child structure and outputs","输入、内部子级与输出")});
  const counts=[0,0,0];for(const p of points){p.x=48+p.column*108;p.y=52+counts[p.column]++*52;}
  const height=Math.max(115,Math.max(...counts)*52+30);svg.setAttribute("viewBox",`0 0 312 ${height}`);
  [tr("INPUTS","输入"),tr("STRUCTURE","内部结构"),tr("OUTPUTS","输出")].forEach((title,i)=>svg.append(svgElement("text",{x:48+i*108,y:15,"text-anchor":"middle",class:"column-label"},title)));
  for(const [a,b] of edges){const p=points.find(p=>p.id===a),q=points.find(p=>p.id===b);if(p&&q)svg.append(svgElement("path",{d:`M${p.x} ${p.y} C${(p.x+q.x)/2+18} ${p.y} ${(p.x+q.x)/2+18} ${q.y} ${q.x} ${q.y}`}));}
  for(const p of points){const g=svgElement("g",{role:"button",tabindex:0,"aria-label":p.title});g.append(svgElement("title",{},p.title),svgElement("circle",{cx:p.x,cy:p.y,r:5,class:["incoming","internal","outgoing"][p.column]}),svgElement("text",{x:p.x,y:p.y+20,"text-anchor":"middle"},p.title.length>14?p.title.slice(0,13)+"…":p.title));g.onclick=guarded(()=>{closeGraphCard();return select(p.id,false);});g.onkeydown=e=>{if(e.key==="Enter")g.onclick();};svg.append(g);}
  section.append(svg);if(members.length>refs.length)section.append(el("p","card-empty",tr(`Previewing ${refs.length} of ${members.length} declarations.`,`预览 ${members.length} 个声明中的 ${refs.length} 个。`)));box.append(section);
}

function balanceMarginHeadings() {
  document.querySelectorAll('#document .section-shell').forEach(shell=>{
    const label=shell.querySelector(':scope > .section-rail .rail-label');
    const first=shell.querySelector(':scope > .segment, :scope > .section-body');
    if(label&&first)first.style.minHeight=`${label.offsetHeight+8}px`;
  });
}
window.addEventListener('resize',balanceMarginHeadings);
