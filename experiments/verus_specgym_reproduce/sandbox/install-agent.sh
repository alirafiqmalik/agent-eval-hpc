#!/bin/bash
set -euo pipefail
revision=3ea751c087f32b16e039a2233dd6eefecef325d5
python3 -m venv /opt/sweagent-venv
/opt/sweagent-venv/bin/pip install --require-hashes -r /opt/specgym-agent-requirements.lock
git init -q /opt/sweagent-repo
git -C /opt/sweagent-repo remote add origin https://github.com/SWE-agent/SWE-agent.git
git -C /opt/sweagent-repo fetch -q --depth 1 origin "$revision"
git -C /opt/sweagent-repo checkout -q --detach FETCH_HEAD
test "$(git -C /opt/sweagent-repo rev-parse HEAD)" = "$revision"
/opt/sweagent-venv/bin/pip install --no-build-isolation --no-deps /opt/sweagent-repo
/opt/sweagent-venv/bin/pip check
site_packages=$(/opt/sweagent-venv/bin/python -c 'import site; print(site.getsitepackages()[0])')
cp -r /opt/sweagent-repo/config "$site_packages/config"
cp -r /opt/sweagent-repo/tools "$site_packages/tools"
mkdir -p "$site_packages/trajectories" /opt/sweagent-configs
cp /opt/sweagent-repo/config/default*.yaml /opt/sweagent-configs/
cat > /usr/local/bin/sweagent <<'SH'
#!/bin/bash
source /opt/sweagent-venv/bin/activate
exec python -m sweagent.run.run "$@"
SH
chmod +x /usr/local/bin/sweagent
printf '%s\n' "$revision" > /opt/specgym-agent-revision
/opt/sweagent-venv/bin/python /opt/specgym-agent-preflight.py \
    --expected-revision "$revision" --output /opt/specgym-agent-runtime.json
/opt/sweagent-venv/bin/pip freeze > /opt/specgym-agent-freeze.txt
