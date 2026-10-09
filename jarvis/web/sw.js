// Lets the browser install Jarvis as an app. Everything goes to the network: Jarvis is live.
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));
self.addEventListener("fetch", () => {});
