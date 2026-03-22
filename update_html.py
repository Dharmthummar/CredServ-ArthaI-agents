import sys

try:
    with open('index.html', 'r', encoding='utf-8') as f:
        html = f.read()

    changes = 0

    # 1. Update Fonts Link safely
    start = html.find('<link')
    if start != -1:
        # find the googleapis css link specifically
        start_href = html.find('href="https://fonts.googleapis.com/css2', start)
        if start_href != -1:
            # Step back to the start of <link>
            true_start = html.rfind('<link', 0, start_href)
            end = html.find('>', start_href) + 1
            if true_start != -1 and end > true_start:
                html = html[:true_start] + '<link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;500;600;700;800&family=Playfair+Display:ital,wght@0,500;0,600;0,700;1,500;1,600&family=DM+Mono:wght@400;500&display=swap" rel="stylesheet" />' + html[end:]
                changes += 1

    # 2. Update CSS font families
    html = html.replace("font-family: 'DM Sans', sans-serif;", "font-family: 'Outfit', sans-serif;")
    html = html.replace("font-family: 'Syne', sans-serif;", "font-family: 'Playfair Display', serif;")

    # 3. Modify Body Layout CSS safely
    body_marker = "body {"
    start_body_css = html.find(body_marker)
    if start_body_css != -1:
        end_body_css = html.find("}", start_body_css) + 1
        new_body_css = '''body {
      font-family: 'Outfit', sans-serif;
      background: var(--bg);
      color: var(--text);
      margin: 0;
      padding: 0;
      overflow-x: hidden;
    }
    #dashboardApp {
      display: flex;
      min-height: 100vh;
      opacity: 0;
      visibility: hidden;
      position: absolute;
      top: 0;
      left: 0;
      width: 100%;
    }'''
        html = html[:start_body_css] + new_body_css + html[end_body_css:]
        changes += 1

    # 4. Add Landing Page CSS
    landing_css = '''
    /* ── LANDING PAGE ── */
    #landingPage {
      position: relative;
      width: 100vw;
      height: 100vh;
      background: linear-gradient(135deg, #020617 0%, #0f172a 100%);
      color: #fff;
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      overflow: hidden;
      z-index: 1000;
      transition: opacity 0.8s ease, transform 0.8s ease;
    }
    .landing-bg-glow {
      position: absolute;
      width: 600px;
      height: 600px;
      background: radial-gradient(circle, rgba(0, 166, 81, 0.15) 0%, transparent 70%);
      top: 50%;
      left: 50%;
      transform: translate(-50%, -50%);
      z-index: 1;
      pointer-events: none;
    }
    .landing-content {
      position: relative;
      z-index: 2;
      text-align: center;
      max-width: 800px;
      padding: 0 20px;
    }
    .landing-pill {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      padding: 6px 16px;
      background: rgba(255, 255, 255, 0.05);
      border: 1px solid rgba(255, 255, 255, 0.1);
      border-radius: 30px;
      font-size: 13px;
      font-weight: 500;
      color: #94a3b8;
      margin-bottom: 24px;
      letter-spacing: 1px;
      text-transform: uppercase;
    }
    .landing-title {
      font-family: 'Playfair Display', serif;
      font-size: 64px;
      font-weight: 600;
      line-height: 1.1;
      margin-bottom: 24px;
      background: linear-gradient(to right, #ffffff, #cbd5e1);
      -webkit-background-clip: text;
      -webkit-text-fill-color: transparent;
      letter-spacing: -1px;
    }
    .landing-title i {
      font-style: italic;
      color: #4ade80;
      -webkit-text-fill-color: initial;
      background: none;
    }
    .landing-desc {
      font-size: 18px;
      color: #94a3b8;
      line-height: 1.6;
      margin-bottom: 40px;
      font-weight: 300;
    }
    .btn-landing {
      background: #00a651;
      color: #fff;
      border: none;
      padding: 16px 36px;
      font-size: 15px;
      font-family: 'Outfit', sans-serif;
      font-weight: 600;
      border-radius: 8px;
      cursor: pointer;
      display: inline-flex;
      align-items: center;
      gap: 10px;
      transition: all 0.3s ease;
      box-shadow: 0 10px 30px rgba(0, 166, 81, 0.3);
    }
    .btn-landing:hover {
      transform: translateY(-2px);
      box-shadow: 0 15px 40px rgba(0, 166, 81, 0.4);
      background: #00b85c;
    }
    .landing-nav {
      position: absolute;
      top: 0;
      width: 100%;
      padding: 30px 50px;
      display: flex;
      justify-content: space-between;
      align-items: center;
      z-index: 2;
    }
    .landing-logo {
      font-family: 'Playfair Display', serif;
      font-size: 20px;
      font-weight: 700;
      color: #fff;
      display: flex;
      align-items: center;
      gap: 10px;
    }
'''

    if '/* ── SVG ICONS ── */' in html:
        html = html.replace('/* ── SVG ICONS ── */', landing_css + '\n    /* ── SVG ICONS ── */', 1)
        changes += 1

    # 5. Inject HTML
    landing_html = '''
  <div id="landingPage">
    <div class="landing-bg-glow"></div>
    <div class="landing-nav">
      <div class="landing-logo">
        <svg width="24" height="24" viewBox="0 0 40 40" fill="none" xmlns="http://www.w3.org/2000/svg">
          <rect x="6" y="6" width="28" height="28" rx="6" transform="rotate(15 6 6)" fill="#006633" opacity=".9" />
          <rect x="8" y="8" width="24" height="24" rx="5" transform="rotate(-10 8 8)" fill="#00a651" opacity=".85" />
        </svg>
        CredServ
      </div>
      <a href="https://github.com/Dharmthummar/CredServ-ArthaI-agents" target="_blank" style="color:#fff;text-decoration:none;font-size:14px;font-weight:500;padding:8px 16px;border:1px solid rgba(255,255,255,0.2);border-radius:20px;transition:all 0.2s" onmouseover="this.style.background='rgba(255,255,255,0.1)'" onmouseout="this.style.background='transparent'">GitHub Repository</a>
    </div>
    <div class="landing-content">
      <div class="landing-pill">
        <span style="width:6px;height:6px;background:#4ade80;border-radius:50%;display:inline-block;box-shadow:0 0 8px #4ade80"></span>
        ArthaI AI Agents Platform
      </div>
      <h1 class="landing-title">Intelligent Workflows for <i>Modern</i> FinTech.</h1>
      <p class="landing-desc">Explore our production-grade ecosystem featuring an LLM-powered KYC Extractor and a deterministic Collections Orchestrator. Fully bounded, auditable, and built for RBI compliance.</p>
      <button class="btn-landing" onclick="enterDashboard()">
        Launch Interactive Demo
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
          <line x1="5" y1="12" x2="19" y2="12" />
          <polyline points="12 5 19 12 12 19" />
        </svg>
      </button>
    </div>
  </div>
  <div id="dashboardApp">
'''

    if '<body>' in html:
        html = html.replace('<body>', '<body>\n' + landing_html, 1)
        changes += 1

    # Close the dashboardApp div before </body> and add JS
    js_inject = '''
    // ── Landing Page Transition ──
    function enterDashboard() {
      const lp = document.getElementById('landingPage');
      const app = document.getElementById('dashboardApp');
      
      lp.style.transform = 'translateY(-20px) scale(0.98)';
      lp.style.opacity = '0';
      
      setTimeout(() => {
        lp.style.display = 'none';
        app.style.visibility = 'visible';
        app.style.position = 'relative';
        app.style.opacity = '1';
        app.style.animation = 'fadeInUp 0.6s cubic-bezier(0.16, 1, 0.3, 1) forwards';
      }, 600);
    }
'''
    if '</script>' in html:
        html = html.replace('</script>', js_inject + '\n  </script>')
        changes += 1

    # Add fadeInUp keyframes
    if '@keyframes fadeIn {' in html:
        html = html.replace('@keyframes fadeIn {', '@keyframes fadeInUp { from { opacity: 0; transform: translateY(20px); } to { opacity: 1; transform: translateY(0); } }\n    @keyframes fadeIn {')
        changes += 1

    if '</body>' in html:
        html = html.replace('</body>', '  </div>\n</body>')
        changes += 1

    with open('index.html', 'w', encoding='utf-8') as f:
        f.write(html)

    print(f'Success, {changes} sections modified')

except Exception as e:
    print('Error:', e)
