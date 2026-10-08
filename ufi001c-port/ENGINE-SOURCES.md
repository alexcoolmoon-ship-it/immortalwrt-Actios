# XHTTP engine provenance and local changes

Podkop 0.7.23: https://github.com/itdoginfo/podkop/releases/tag/0.7.23
Its README links to FiyeroT/podkop-engine for XHTTP. This is an unofficial
sing-box derivative, not an official SagerNet build.

Engine: https://github.com/FiyeroT/podkop-engine/releases/tag/v1.13.21-r12
Commit: 46e23704bbee712b192ec7d519ad906b727f1c0e.
Base sing-box: 1.13.21. Patches: v1.13, 0001–0086, copied without changes.
Upstream package's main variant (podkop_slim) is used. The full/Naive variant
is not built. XHTTP and DoH are included in the main variant.

addons.lock.json records source hashes and versions. The 86 patches are kept
in podkop-engine-patches.tar.gz to simplify GitHub web upload. prepare_engine.py
checks the archive and each patch against the manifest before copying them.
Source tarball hashes are enforced by the OpenWrt download/build system.

Go 1.26.8 is used as in the engine release. Its OpenWrt Makefile comes from
packages commit f60a090afff932579d015045dd1eff9f744fa043. Compared with the pinned
feed's Go 1.26.4 recipe, only patch version and source hash change. No other
package feed is moved to a floating branch.

Local changes to the package: omit BuildPackage for podkop-engine-full;
provide fixed version.mk; default dns_fallback and failsafe to 0. Ordinary
sing-box/sing-box-tiny providers are disabled and Podkop depends explicitly
on podkop-engine, so Kconfig cannot silently select the incompatible engine.
The installed executable and service are still named sing-box.

DNS policy: normal Podkop DNS uses DoH. Engine fallback to ISP DNS and automatic
crash-loop bypass are disabled. If DoH or the engine fails, Internet name
resolution may fail until fixed; LAN administration remains available. A
manual Podkop stop or ufi-care network recovery returns ordinary network
settings, so this is not a system-wide guarantee against all plaintext DNS.
Applications with their own DNS/VPN are outside this setting.

Validation performed locally: all 86 patches apply with fuzz=0; upstream r12
ARM64 binary was executed under QEMU and passed the included decoder/config
checks. This is not a claim that our final firmware was already built or that
an actual XHTTP connection to the user's server succeeded. The workflow runs
the same gate against its own newly built ARM64 binary before publishing.

Licenses: engine repo and sing-box licenses are included beside the recipe.
No user proxy links, server credentials or individual modem NV are bundled.
