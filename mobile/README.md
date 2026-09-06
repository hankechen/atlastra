# Atlastra mobile shell (Capacitor)

Native iOS/Android wrapper around the live site (`https://atlastra.dedyn.io`) for
submission to the App Store and Google Play. This is a **thin shell, not a
rebuild** — `capacitor.config.json`'s `server.url` points the native WebView
straight at the live site, so there's no separate web build to keep in sync;
the app always shows exactly what the browser shows. `www/` is an unused
placeholder Capacitor's tooling requires to exist.

- App id: `com.atlastra.app`
- Source icon/splash art: `assets/` (same triangle mark as `webapp/frontend/favicon.svg`,
  regenerated at print resolution — see [[pwa-and-app-store]] memory for how, if it
  ever needs to change)
- Generated native icons/splash: `ios/App/App/Assets.xcassets/`,
  `android/app/src/main/res/mipmap-*` + `drawable*` (regenerate both with the
  command below after replacing anything in `assets/`)

## One-time setup (already done on this machine)

```
npm install
npx cap add ios       # needs CocoaPods (brew install cocoapods) + full Xcode selected:
                       #   sudo xcode-select -s /Applications/Xcode.app/Contents/Developer
npx cap add android    # needs nothing extra to scaffold; Android Studio only needed to build/run
npx capacitor-assets generate --ios --android \
  --iconBackgroundColor '#0e1220' --iconBackgroundColorDark '#0e1220' \
  --splashBackgroundColor '#0e1220' --splashBackgroundColorDark '#0e1220'
```

## Day to day

```
npx cap open ios       # opens Xcode -- run on a simulator/device, or Archive to submit
npx cap open android   # opens Android Studio -- run, or Build > Generate Signed Bundle
```

Nothing to "sync" from a web build (no `npm run build` step here) — only re-run
`npx cap sync` if you add/remove a Capacitor plugin, or `capacitor-assets generate`
again if you replace the source art in `assets/`.

## Still pending (needs a human, not just tooling)

1. **iOS:** run `sudo xcode-select -s /Applications/Xcode.app/Contents/Developer`
   once on this Mac (this repo's assistant can't run `sudo`) — until then
   `cap sync ios` / `pod install` fail with "requires Xcode".
2. **Android:** install Android Studio (or at least the command-line SDK tools) to
   actually build/run/sign the app locally — the Gradle project scaffold exists
   and syncs, but nothing here can build an APK/AAB without the Android SDK.
3. **Apple Developer Program** enrollment ($99/yr) — required for `cap open ios` →
   Archive → distribute, and for App Store Connect. Same for the App Store
   listing (screenshots, description, privacy policy URL, age rating, etc.).
4. **Google Play Console** account ($25 one-time) — required to create the app
   listing and upload a signed AAB. Play also wants a signed **Digital Asset
   Links** file if you ever add Android App Links, but is NOT required just to
   ship this Capacitor app (that's only a TWA requirement).
5. **A release signing keystore** (Android) / **distribution certificate +
   provisioning profile** (iOS, via Xcode's automatic signing once you're in
   the Developer Program) — neither exists yet, generate when first archiving
   for release. Never commit the `.keystore`/`.jks` file (already gitignored).
6. **Push notifications**, if you want the real fix for "alerts only fire while
   the tab's open" (the existing bell uses the browser Notification API, gated
   on `Notification.permission` in `js/api.js` — see the desktop-notifications
   fix in git history) — would mean adding `@capacitor/push-notifications` +
   APNs/FCM setup + a small server-side push sender. Not started; the web
   Notification API path is unaffected either way.
