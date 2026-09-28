# Wiring up manifest.json + service-worker.js on tapin-ispsctagudin.vercel.app

1. Copy `manifest.json` and `service-worker.js` into the root of your
   deployed site (same level as `login.html`), so they're reachable at:
   - `https://tapin-ispsctagudin.vercel.app/manifest.json`
   - `https://tapin-ispsctagudin.vercel.app/service-worker.js`

2. In `login.html` (and ideally every dashboard page), add this inside
   `<head>`:

   ```html
   <link rel="manifest" href="/manifest.json" />
   <meta name="theme-color" content="#2C0A12" />
   ```

3. Register the service worker — add this near the end of the page,
   before `</body>`:

   ```html
   <script>
     if ('serviceWorker' in navigator) {
       navigator.serviceWorker.register('/service-worker.js');
     }
   </script>
   ```

4. **Icons** — `manifest.json` currently points at your existing
   `/assets/tapin_icon.png` for all sizes. For a good installability
   score (and a crisp icon in the Play Store / on the home screen), swap
   in real 192x192 and 512x512 PNG exports of your logo if you have them
   — same filename references work, just replace the actual image files,
   or add new files and update the `src` paths in `manifest.json`.

5. Once live, test it: open the site in Chrome on Android — you should
   see a native "Add TapIn to Home screen" prompt. That confirms the PWA
   is wired up correctly and PWABuilder (see the main README, Option C)
   will be able to package it into a real APK/AAB.
