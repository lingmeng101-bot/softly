from crawler.sites import get_parser, host_of, qiaomu

LIST_HTML = """
<html><body>
<noscript class="seo-fallback">
  <h2>热门 Skill</h2>
  <ul>
    <li><a href="/skills/install%3Areact%3Avercel-labs-agent-skills-vercel-react-best-practices/">vercel-react-best-practices</a>：基于 Vercel Engineering 的 React 与 Next.js 性能规则。</li>
    <li><a href="/skills/install%3Areact%3Ashadcn-ui-shadcn/">shadcn</a>：围绕 shadcn/ui 的组件新增与样式调整。</li>
    <li><a href="/categories/install-react/">前端与 React</a></li>
  </ul>
</noscript>
</body></html>
"""

DETAIL_HTML = """
<html><body>
<main class="app-shell">
  <header class="detail-hero">
    <h1 class="detail-title">vercel-react-best-practices</h1>
    <p class="detail-value">基于 Vercel Engineering 的 React 与 Next.js 性能规则。</p>
    <p class="detail-fit">写 React/Next.js 页面与性能排查时。</p>
  </header>
  <section class="detail-top-actions">
    <div class="detail-command"><code>npx skills add https://github.com/vercel-labs/agent-skills --skill vercel-react-best-practices</code></div>
  </section>
  <div class="detail-layout">
    <div class="detail-main">
      <section>
        <h2 class="detail-section-title">为什么值得装</h2>
        <p>在分类里热度靠前，原文信息密度高。</p>
      </section>
      <section>
        <h2 class="detail-section-title">原文里最有价值的点</h2>
        <ul class="detail-list"><li>覆盖组件、路由与打包体积。</li></ul>
      </section>
    </div>
    <aside class="detail-sidebar">
      <section class="detail-side-panel">分类 前端与 React 来源 vercel-labs/ agent-skills</section>
    </aside>
  </div>
</main>
</body></html>
"""


def test_parse_list_skills():
    items = qiaomu.parse_list(LIST_HTML, "https://skills.qiaomu.ai/")
    assert len(items) == 2
    assert items[0]["url"] == "https://skills.qiaomu.ai/skills/install%3Areact%3Avercel-labs-agent-skills-vercel-react-best-practices/"
    assert items[0]["title"] == "vercel-react-best-practices"
    assert "Vercel Engineering" in items[0]["summary"]
    assert items[1]["title"] == "shadcn"


def test_parse_detail_content():
    text = qiaomu.parse_detail(DETAIL_HTML)
    assert "vercel-react-best-practices" in text
    assert "基于 Vercel Engineering" in text
    assert "安装命令：npx skills add" in text
    assert "为什么值得装" in text and "热度靠前" in text
    assert "覆盖组件、路由与打包体积" in text
    assert "前端与 React" in text


def test_parse_detail_blocked_on_challenge():
    assert qiaomu.parse_detail("<html><title>Just a moment...</title></html>") == "blocked"


def test_parser_routing():
    assert get_parser("https://skills.qiaomu.ai/") is qiaomu
    assert host_of("https://skills.qiaomu.ai/categories/install-react/") == "skills.qiaomu.ai"
