"""Deploy the exact clean Git commit to this side project's dedicated collector only."""
import argparse
import json
from pathlib import Path
import re
import subprocess

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--profile', default='oliver-admin')
args = parser.parse_args()
root = Path(__file__).resolve().parents[1]
state = json.loads((root / 'deploy/production.json').read_text())
if subprocess.check_output(['git','status','--porcelain'],cwd=root).strip():
    raise SystemExit('Commit the validated source before deployment.')
sha = subprocess.check_output(['git','rev-parse','HEAD'],cwd=root,text=True).strip()
assert re.fullmatch(r'[a-f0-9]{40}',sha)
commands = ['set -eu','cd /opt/poly-metars',
            'test -z "$(git status --porcelain)"',
            'git fetch origin main', 'previous=$(git rev-parse HEAD)',
            'systemctl stop poly-metars.service', 'git checkout '+sha,
            'if ! python3 -m unittest discover -s tests -q; then git checkout "$previous"; systemctl start poly-metars.service; exit 1; fi',
            'install -m 0644 deploy/poly-metars.service /etc/systemd/system/poly-metars.service',
            'systemctl daemon-reload','systemctl restart poly-metars.service',
            'systemctl is-active poly-metars.service']
result = subprocess.check_output(['aws','ssm','send-command','--profile',args.profile,
                                 '--region',state['region'],'--instance-ids',state['instance_id'],
                                 '--document-name','AWS-RunShellScript','--parameters',json.dumps({'commands':commands}),
                                 '--comment','Deploy poly-metars '+sha,'--output','json'],text=True)
print(json.dumps({'commit':sha,'command_id':json.loads(result)['Command']['CommandId'],'instance_id':state['instance_id']}))
