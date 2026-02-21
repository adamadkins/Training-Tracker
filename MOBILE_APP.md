# Training Tracker – App Store / Play Store build (Capacitor)

The app is a **wrapper** around your live site. Users enter their company subdomain (or choose Platform admin) and the app loads that org’s login in a full-screen WebView. Invite emails use an “open in app” link so invitees can open the app and go straight to their company.

## Change the app URL

Edit **`web/index.html`** and replace `https://trainingtracker.me` with your real production URL (in both the `<meta http-equiv="Refresh">` and the `window.location.replace()` call, and the fallback link).

## Workflow

1. **Sync web assets into native projects** (after any change to `web/` or config):
   ```bash
   npm run cap:sync
   ```
2. **Android** (build on Windows or Mac):
   ```bash
   npm run cap:android
   ```
   This opens the `android` folder in Android Studio. Then: **Build → Build Bundle(s) / APK(s)** and use **Build → Generate Signed Bundle / APK** for Play Store.

3. **iOS** (build on **Mac only**, with Xcode):
   ```bash
   npm run cap:ios
   ```
   This opens the `ios` folder in Xcode. Then: select a simulator or device, run. For App Store: **Product → Archive**, then distribute via App Store Connect.

## Store requirements

- **Apple:** [Apple Developer Program](https://developer.apple.com/programs/) ($99/year), then create an app in App Store Connect and submit the archive.
- **Google:** [Google Play Console](https://play.google.com/console) (one-time fee), then create an app and upload the AAB from Android Studio.

## Invite links and deep links

- When a manager invites a user, the email link goes to `https://trainingtracker.me/open-in-app?redirect=...` so the invitee can choose “Open in app” or “Continue in browser.” “Open in app” uses the `trainingtracker://open?url=...` scheme so the native app opens and loads the set-password (or login) URL in the iframe.
- The app handles launch URLs: if opened via `trainingtracker://open?url=ENCODED_URL`, it loads that URL directly (e.g. set-password or company login).
- **Platform admin:** On the app’s first screen, “Platform admin?” opens the main domain login so superusers can sign in and use `/admin` on the main site.

## Notes

- The app ID is `me.trainingtracker.app` (in `capacitor.config.json`). Change it if you want a different bundle ID.
- After adding or changing npm dependencies (e.g. `@capacitor/app`), run `npm install` then `npm run cap:sync`.
- To test on a device: run `npm run cap:sync`, then open the project in Android Studio or Xcode and run on a connected device or emulator.
