#!/usr/bin/env python3
"""Generate a single-device config: gen_config.py SOURCE DESTINATION."""
import os
from pathlib import Path
import re
import sys
import tempfile

DEVICES = [
    "ufi001c", "ufi001b", "ufi103s", "qrzl903", "w001",
    "ufi003", "uz801", "mf32", "mf601", "wf2", "jz02v10", "sp970v11", "sp970v10"
]
FIRMWARE = (
    "modem-openstick-{}-firmware",
    "openstick-{}-wcnss-firmware",
    "wcnss-openstick-{}-nv",
)
TARGET = "CONFIG_TARGET_msm89xx_msm8916_DEVICE_openstick-"


def replace_line(content, old, new):
    pattern = re.compile(r"^" + re.escape(old) + r"$", re.MULTILINE)
    if len(pattern.findall(content)) != 1:
        raise ValueError("Expected exactly one config entry: " + old)
    return pattern.sub(lambda _: new, content)


def validate_config(content, device):
    targets = re.findall(r"^CONFIG_TARGET_\S*_DEVICE_\S+=y$", content, re.MULTILINE)
    if targets != [TARGET + device + "=y"]:
        raise ValueError("Template must select exactly the source device: " + device)
    profiles = re.findall(r"^CONFIG_TARGET_PROFILE=.*$", content, re.MULTILINE)
    if profiles != ['CONFIG_TARGET_PROFILE="DEVICE_openstick-' + device + '"']:
        raise ValueError("Template TARGET_PROFILE does not match: " + device)
    for prefix in ("DEFAULT", "PACKAGE"):
        for suffix in FIRMWARE:
            expected = "CONFIG_" + prefix + "_qcom-msm8916-" + suffix.format(device) + "=y"
            expression = re.escape(suffix).replace(re.escape("{}"), r"[A-Za-z0-9_-]+")
            selected = re.findall(
                r"^CONFIG_" + prefix + "_qcom-msm8916-" + expression + r"=y$",
                content, re.MULTILINE)
            if selected != [expected]:
                raise ValueError("Template must select matching device firmware: " + expected)


def convert_config(content, src, dst):
    if src not in DEVICES or dst not in DEVICES:
        raise ValueError("Unsupported device; supported: " + ", ".join(DEVICES))
    validate_config(content, src)
    if src == dst:
        return content
    symbols = [TARGET + "{}"]
    symbols.extend("CONFIG_" + prefix + "_qcom-msm8916-" + suffix
                   for prefix in ("DEFAULT", "PACKAGE") for suffix in FIRMWARE)
    for symbol in symbols:
        content = replace_line(content, symbol.format(src) + "=y",
                               "# " + symbol.format(src) + " is not set")
        content = replace_line(content, "# " + symbol.format(dst) + " is not set",
                               symbol.format(dst) + "=y")
    content = replace_line(content, 'CONFIG_TARGET_PROFILE="DEVICE_openstick-' + src + '"',
                           'CONFIG_TARGET_PROFILE="DEVICE_openstick-' + dst + '"')
    validate_config(content, dst)
    return content


def gen_config(src, dst):
    if src not in DEVICES or dst not in DEVICES:
        raise ValueError("Unsupported device; supported: " + ", ".join(DEVICES))
    src_file, dst_file = Path("config") / (src + ".config"), Path("config") / (dst + ".config")
    content = convert_config(src_file.read_text(encoding="utf-8"), src, dst)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n",
                                         dir=dst_file.parent, prefix=".gen-config-",
                                         delete=False) as output:
            temporary = Path(output.name)
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, dst_file)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    print(f"完成: {dst_file} 已生成 (基于 {src_file})")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    try:
        gen_config(sys.argv[1], sys.argv[2])
    except (OSError, ValueError) as error:
        raise SystemExit("错误: " + str(error))

