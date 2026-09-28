# TapIn RFID Attendance System - iOS App

This is the native iOS application for **TapIn RFID Attendance System**, built with SwiftUI and `WKWebView`.

It wraps the web dashboard at `https://tapin-ispsctagudin.vercel.app/login.html` into a native iOS app experience:
- Keeps login session persistent (cookies + DOM localStorage)
- Full startup screen overlay with TapIn logo, title, and gold accent spinner
- Camera and Photo Library permissions enabled for profile photo uploads & badge / QR scanning
- Custom offline retry screen when network connection is unavailable
- Native iOS dark theme styling (`#2C0A12` maroon & `#D4AF37` gold)

---

## Building and Running on iOS

### Requirements
- macOS with **Xcode 15+** installed (free on Mac App Store)
- iOS 15.0+ device or iPhone Simulator

### How to Open & Build in Xcode

1. Open **Xcode**.
2. Select **Open a project or file** (or `File > Open...`).
3. Select the folder:
   `ios/TapIn/TapIn.xcodeproj`
4. Choose an iPhone Simulator (e.g., *iPhone 15 Pro*) or your connected iPhone from the top toolbar.
5. Press `Cmd + R` (or click the **Play** ▶ button) to build and run the app.

---

## App Details & Configuration

- **Bundle ID:** `com.tapin.attendance`
- **Main Web Dashboard URL:** `https://tapin-ispsctagudin.vercel.app/login.html`
- **Camera Permissions:** Configured in `Info.plist` (`NSCameraUsageDescription`, `NSPhotoLibraryUsageDescription`, `NSMicrophoneUsageDescription`).
