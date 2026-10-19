const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

async function run() {
  const source = fs.readFileSync(path.join(__dirname,
    '../clean/package/openstick/luci-app-podkop/htdocs/luci-static/resources/view/podkop/settings.js'), 'utf8');
  let response, reject, calls = [], notifications = [];
  const options = {};
  const state = { settings: { '.type': 'settings' }, main: { '.type': 'section', connection_type: 'proxy' },
    second: { '.type': 'section', connection_type: 'proxy' }, vpn: { '.type': 'section', connection_type: 'vpn' },
    block: { '.type': 'section', connection_type: 'block' }, exclusion: { '.type': 'section', connection_type: 'exclusion' } };
  const section = { option(type, name, title, description) {
    const option = { type, title, description, values: [], dependencies: [],
      map: { data: { state: { values: { podkop: state } } } },
      value(...args) { this.values.push(args); },
      depends(...args) { this.dependencies.push(args); } };
    options[name] = option;
    return option;
  } };
  const form = { Flag: 'Flag', Button: 'Button', ListValue: 'ListValue', Value: 'Value' };
  const main = { DNS_SERVER_OPTIONS: {}, BOOTSTRAP_DNS_SERVER_OPTIONS: {}, UPDATE_INTERVAL_OPTIONS: {}, getClashUIUrl: () => '' };
  const rpc = { async exec(command, args) { calls.push([command, args]); if (reject) throw new Error('RPC'); return response; } };
  const ui = { addNotification(...args) { notifications.push(args); } };
  const module = new Function('form', 'uci', 'fs', 'ui', 'baseclass', 'widgets', 'main', '_', 'E', source)(
    form, {}, rpc, ui, { extend: x => x }, {}, main, x => x, (tag, text) => ({ tag, text }));
  module.createSettingsContent(section);
  assert.equal(options.cloudflare_dns_patch.default, '1');
  assert.equal(options.cloudflare_bootstrap_type.default, 'udp');
  assert.deepEqual(options.cloudflare_bootstrap_type.values, [['udp', 'UDP'], ['tcp', 'TCP']]);
  assert.deepEqual(options.cloudflare_bootstrap_type.dependencies, [['cloudflare_dns_patch', '1']]);
  for (const name of ['dns_type', 'dns_server', 'bootstrap_dns_server'])
    assert.deepEqual(options[name].dependencies, [['cloudflare_dns_patch', '0']]);
  await options.cloudflare_dns_section.load();
  assert.deepEqual(options.cloudflare_dns_section.keylist, ['', 'main', 'second']);
  const probe = options._cloudflare_check;
  assert.equal(probe.type, 'Button');
  assert.deepEqual(probe.dependencies, [['cloudflare_dns_patch', '1']]);
  for (const [value, code, expected] of [
    [{ mode: 'cloudflare_proxy', dns_status: 1 }, 0, 'info'],
    [{ mode: 'cloudflare_proxy', dns_status: 0 }, 0, 'error'],
    [{ mode: 'custom', dns_status: 1 }, 0, 'warning'],
    [{ mode: 'cloudflare_proxy', dns_status: '1' }, 0, 'error'],
    [{ mode: 'cloudflare_proxy', dns_status: 1 }, 1, 'error'],
    [null, 0, 'error'],
  ]) {
    response = { code, stdout: JSON.stringify(value) };
    notifications = [];
    await probe.onclick();
    assert.equal(notifications.length, 1);
    assert.equal(notifications[0][2], expected);
  }
  reject = true;
  await probe.onclick();
  assert.equal(notifications.at(-1)[2], 'error');
  assert.ok(calls.every(call => call[0] === '/usr/bin/podkop' && call[1].join() === 'cloudflare_status'));
  console.log('Cloudflare UI: controls, proxy selection, real result handling and RPC errors passed');
}
run().catch(error => { console.error(error); process.exitCode = 1; });
