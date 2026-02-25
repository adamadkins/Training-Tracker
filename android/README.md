# Training Tracker – Android

## Build (important)

**Before every build**, sync web assets into the app so the WebView has content. Otherwise the app will show a black screen.

From the project root:

```bash
npx cap sync android
```

Then build the APK (from this `android` folder or Android Studio):

```bash
cd android
./gradlew assembleDebug
```

Or open in Android Studio and build from there.

## Publish

For release: configure signing in `app/build.gradle`, then run `./gradlew assembleRelease`.
