import SwiftUI
import WebKit

struct ContentView: View {
    @StateObject private var webViewModel = WebViewModel()
    @State private var isOffline = false

    private let homeURL = URL(string: "https://tapin-ispsctagudin.vercel.app/login.html")!

    var body: some View {
        ZStack {
            Color(red: 0.17, green: 0.04, blue: 0.07) // TapIn Maroon Dark
                .ignoresSafeArea()

            if isOffline {
                OfflineView {
                    isOffline = false
                    webViewModel.reload()
                }
            } else {
                VStack(spacing: 0) {
                    WebView(url: homeURL, viewModel: webViewModel, isOffline: $isOffline)
                        .ignoresSafeArea(.container, edges: .bottom)
                }
            }

            // Startup Splash Overlay with Logo & Branding
            if webViewModel.isLoading {
                SplashOverlayView()
                    .transition(.opacity)
            }
        }
        .animation(.easeOut(duration: 0.3), value: webViewModel.isLoading)
    }
}

struct SplashOverlayView: View {
    var body: some View {
        ZStack {
            Color(red: 0.17, green: 0.04, blue: 0.07)
                .ignoresSafeArea()

            VStack(spacing: 16) {
                // TapIn Emblem / Logo
                ZStack {
                    Circle()
                        .stroke(Color(red: 0.83, green: 0.69, blue: 0.22), lineWidth: 3)
                        .frame(width: 100, height: 100)
                        .background(
                            Circle()
                                .fill(Color(red: 0.50, green: 0.00, blue: 0.13))
                        )

                    Image(systemName: "wave.3.right.circle.fill")
                        .resizable()
                        .scaledToFit()
                        .frame(width: 50, height: 50)
                        .foregroundColor(Color(red: 0.83, green: 0.69, blue: 0.22))
                }

                Text("TapIn")
                    .font(.system(size: 32, weight: .bold))
                    .foregroundColor(Color(red: 0.83, green: 0.69, blue: 0.22))

                Text("RFID Attendance System")
                    .font(.system(size: 14))
                    .foregroundColor(Color(white: 0.8))

                ProgressView()
                    .progressViewStyle(CircularProgressViewStyle(tint: Color(red: 0.83, green: 0.69, blue: 0.22)))
                    .scaleEffect(1.2)
                    .padding(.top, 20)
            }
        }
    }
}

struct OfflineView: View {
    let onRetry: () -> Void

    var body: some View {
        VStack(spacing: 16) {
            Image(systemName: "wifi.slash")
                .resizable()
                .scaledToFit()
                .frame(width: 64, height: 64)
                .foregroundColor(Color(red: 0.83, green: 0.69, blue: 0.22))

            Text("You're offline")
                .font(.title2.bold())
                .foregroundColor(.white)

            Text("Couldn't reach TapIn. Check your connection and try again.")
                .font(.subheadline)
                .multilineTextAlignment(.center)
                .foregroundColor(Color(white: 0.8))
                .padding(.horizontal, 32)

            Button(action: onRetry) {
                Text("Retry")
                    .font(.headline)
                    .foregroundColor(Color(red: 0.17, green: 0.04, blue: 0.07))
                    .padding(.horizontal, 32)
                    .padding(.vertical, 12)
                    .background(Color(red: 0.83, green: 0.69, blue: 0.22))
                    .cornerRadius(8)
            }
            .padding(.top, 8)
        }
    }
}
