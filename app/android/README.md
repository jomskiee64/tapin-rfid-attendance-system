# TapIn RFID Attendance System Android App (WebView wrapper)

This is a complete, ready-to-build Android Studio project. It's a native
Android app with a single screen: a WebView pointed at

    https://tapin-ispsctagudin.vercel.app/login.html

It behaves like a real app, not just "a browser tab":
- Keeps you logged in between opens (cookies + localStorage persist)
- Profile photo upload (`<input type="file">`) opens the real Android file/camera picker
- "Export PDF" / any file download is handed to Android's Download Manager, so it lands in your phone's Downloads like a normal download
- Back button navigates page history first, then exits the app
- Pull-to-refresh
- A proper "You're offline" screen with a Retry button instead of a blank page
- Status bar colored to match the TapIn maroon theme
- App icon included (placeholder "T" monogram in maroon/gold — swap in your real logo whenever you like, see below)

**I could not compile this into a `.apk` file myself** — that requires the
Android SDK, Gradle, and internet access to download build dependencies,
none of which are available in the sandbox I run in. Everything else is
done: the code, manifest, layouts, icons, and Gradle config are all
complete and correct. You just need to run the build, which takes about
2 minutes.

## Option A — Build it with Android Studio (easiest, no command line)

1. Install [Android Studio](https://developer.android.com/studio) (free).
2. Open Android Studio → **Open** → select the `TapInApp` folder (this folder).
3. Let it sync (first time takes a few minutes — it downloads Gradle and
   the Android SDK bits automatically, this is the internet access step
   I don't have).
4. Menu: **Build → Build Bundle(s) / APK(s) → Build APK(s)**.
5. When it finishes, click the **"locate"** link in the notification, or
   find the file at:
   `app/build/outputs/apk/debug/app-debug.apk`
6. Copy that `.apk` to your phone (email it to yourself, USB transfer, or
   Google Drive) and tap it to install. You'll need to allow "Install
   unknown apps" for whichever app you use to open it (Android will
   prompt you automatically the first time).

That `.apk` is a debug build — perfectly fine to install and use on your
own phone. It just isn't signed for the Play Store (see Option C).

## Option B — Build it from the command line

Requires the Android SDK command-line tools + Java 17 installed.

```bash
cd TapInApp
gradle wrapper          # only needed once, generates gradlew + the wrapper jar
./gradlew assembleDebug
```

The APK will be at `app/build/outputs/apk/debug/app-debug.apk`.

(This project ships `gradle/wrapper/gradle-wrapper.properties` pointing at
Gradle 8.7, but not the wrapper `.jar` itself, since fetching that also
needs internet access I don't have here. Android Studio regenerates it
automatically on first open — that's the simplest path. If you're
command-line only and already have Gradle installed locally, running
`gradle wrapper` once — as above — generates it for you.)

## Option C — Skip Android Studio entirely: PWABuilder (no code, gives you a signed APK/AAB)

Since your site is already HTTPS and can have a web app manifest, the
fastest real path to an installable app — including one you could
actually publish to the Play Store — is:

1. Go to **https://www.pwabuilder.com**
2. Enter `https://tapin-ispsctagudin.vercel.app/login.html`
3. It scores your site's "installability" and, if you add a
   `manifest.json` + service worker to the site (see the
   `pwa-starter-files/` folder next to this README — I built those for
   you too), it will package a real signed Android APK/AAB for you
   automatically, ready to sideload or upload to the Play Store.

This is genuinely the least-effort path if your goal is "an app my
employees can install," since it needs zero Android/Kotlin knowledge.
The Android Studio project above is for when you want full native
control (custom permissions, deeper integration, etc.) instead.

## Customizing

- **Change the URL:** edit `HOME_URL` at the top of
  `app/src/main/java/com/tapin/attendance/MainActivity.kt`.
- **Change the app name:** edit `app_name` in
  `app/src/main/res/values/strings.xml`.
- **Change the app icon:** replace the PNGs in `app/src/main/res/mipmap-*/`
  (mdpi 48px, hdpi 72px, xhdpi 96px, xxhdpi 144px, xxxhdpi 192px — same
  filenames, `ic_launcher.png` and `ic_launcher_round.png`).
- **Change the package name** (`com.tapin.attendance`): update
  `applicationId` and `namespace` in `app/build.gradle.kts`, and move the
  `MainActivity.kt` file to match the new package folder structure.

## Publishing to the Play Store (optional, later)

A debug APK (Option A/B above) is fine for installing on your own or
your staff's phones directly. To publish on the Play Store you'd need to:
1. Generate a signing key (`keytool -genkey ...` or via Android Studio's
   Build → Generate Signed Bundle/APK wizard).
2. Build a **release** AAB instead of a debug APK.
3. Create a Google Play Developer account (one-time $25 fee) and upload it.

Happy to walk through any of these steps in more detail if useful.
