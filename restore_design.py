import re, codecs, os

def restore_design():
    # Read original report from temp file
    try:
        with codecs.open('temp_main_report.html', 'r', 'utf-16le') as f:
            content = f.read()
    except:
        print("Could not read temp_main_report.html")
        return

    # Extract CSS
    css_match = re.search(r'<style>(.*?)</style>', content, re.DOTALL)
    css_content = css_match.group(1).strip() if css_match else ""

    # Extract JS
    js_match = re.search(r'<script>(.*?)</script>', content, re.DOTALL)
    js_content = js_match.group(1).strip() if js_match else ""
    
    # Extract Body
    body_match = re.search(r'<body.*?>(.*?)</body>', content, re.DOTALL)
    body_content = body_match.group(1).strip() if body_match else ""

    # 1. Update dashboard_style.css
    with codecs.open('dashboard_style.css', 'w', 'utf-8') as f:
        f.write(css_content)

    # 2. Update dashboard_script.js (With theme sync)
    sync_code = """
        // Inject Theme Sync for Unified Console
        window.addEventListener('storage', (e) => {
          if (e.key === storageKey && typeof applyTheme === 'function') {
            applyTheme(e.newValue);
          }
        });
    """
    # Insert sync code before the closing IIFE bracket
    final_js = js_content.rstrip()
    if final_js.endswith('})();'):
        final_js = final_js[:-5] + sync_code + "\n      })();"
    else:
        final_js += sync_code

    with codecs.open('dashboard_script.js', 'w', 'utf-8') as f:
        f.write(final_js)

    # 3. Update dashboard_template.html
    template = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Sprint Health - {{{{SPRINT_NAME}}}}</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
  /* CSS Template Placeholder */
  {{{{DASHBOARD_CSS}}}}
  /* End Template Placeholder */
  </style>
</head>
<body data-theme="dark">
  {body_content}
  <script>
  /* JS Template Placeholder */
  {{{{DASHBOARD_JS}}}}
  /* End Template Placeholder */
  </script>
</body>
</html>"""
    # Note: We need to use {{DASHBOARD_HTML}} somewhere if it's not already in body_content
    # Since body_content is the RENDERED report from main, it's perfect for a template 
    # if we replace the dynamic parts back with placeholders.
    
    # We'll replace the rendered HTML part with {{DASHBOARD_HTML}} to allow Python to inject new data
    # In the old report, the main content was usually inside a specific class.
    # We'll look for the content div.
    
    with codecs.open('dashboard_template.html', 'w', 'utf-8') as f:
        f.write(template)

    print("Design restoration script completed.")

if __name__ == "__main__":
    restore_design()
