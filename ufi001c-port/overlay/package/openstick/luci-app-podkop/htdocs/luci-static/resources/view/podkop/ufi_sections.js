"use strict";
"require baseclass";

function candidates(data, dnsOnly) {
  return data.sections("podkop", "section").filter(function (section) {
    const kind = section.connection_type || "proxy";
    if (dnsOnly) {
      return kind === "proxy" && ["url", "outbound"].includes(section.proxy_config_type || "url");
    }
    return kind === "proxy" || kind === "vpn";
  }).map(function (section) { return section[".name"]; });
}

function normalize(value, names) {
  return names.includes(value) ? value : "@auto";
}

function configure(option, dnsOnly) {
  option.default = "@auto";
  option.rmempty = false;
  option.cfgvalue = function (sectionId) {
    return normalize(this.map.data.get("podkop", sectionId, this.option), candidates(this.map.data, dnsOnly));
  };
  option.load = function () {
    // There is always a valid choice, even while deleting the last section.
    // TypedSection.handleAdd/Remove saves the WHOLE LuCI map.
    this.keylist = ["@auto"];
    this.vallist = [_("Automatic (first configured proxy)")];
    for (const name of candidates(this.map.data, dnsOnly)) {
      this.keylist.push(name);
      this.vallist.push(name);
    }
    return Promise.resolve();
  };
  option.write = function (sectionId, value) {
    this.map.data.set("podkop", sectionId, this.option, normalize(value, candidates(this.map.data, dnsOnly)));
  };
}

return baseclass.extend({ candidates, normalize, configure });
