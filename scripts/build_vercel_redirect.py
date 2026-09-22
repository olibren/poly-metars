"""Keep the former Vercel address as a redirect; Cloudflare serves the website."""
import json
from pathlib import Path
import shutil

output = Path('.vercel/output')
if output.exists():
    shutil.rmtree(output)  # Generated build output only; preserve .vercel/project.json.
(output / 'static').mkdir(parents=True)
config = {'version': 3, 'routes': [{'src': '/(.*)', 'status': 307,
    'headers': {'Location': 'https://poly-metars.olibren.workers.dev/$1'}}]}
(output / 'config.json').write_text(json.dumps(config, indent=2)+'\n')
(output / 'static/index.html').write_text('<a href="https://poly-metars.olibren.workers.dev/">Poly METARs has moved to Cloudflare.</a>\n')
print('Built the legacy-address redirect. No observation data is hosted on Vercel.')
