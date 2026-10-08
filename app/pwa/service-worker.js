// PR 328: the service worker, served from /service-worker.js (app/api/pwa.py) so its
// scope is the whole site. The route prepends `self.SW_CONFIG = {...}` - the version and
// the URLs to precache, both derived from the current static files - so any changed
// asset makes this script byte-different and the browser installs the new version.
"use strict";

const CONFIG = self.SW_CONFIG;
