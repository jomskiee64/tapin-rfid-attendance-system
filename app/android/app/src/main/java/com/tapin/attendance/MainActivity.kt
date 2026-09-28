package com.tapin.attendance

import android.Manifest
import android.annotation.SuppressLint
import android.app.DownloadManager
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Bitmap
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import android.net.Uri
import android.os.Bundle
import android.os.Environment
import android.provider.MediaStore
import android.view.View
import android.webkit.CookieManager
import android.webkit.PermissionRequest
import android.webkit.URLUtil
import android.webkit.ValueCallback
import android.webkit.WebChromeClient
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.Button
import android.widget.LinearLayout
import android.widget.ProgressBar
import androidx.activity.addCallback
import androidx.activity.result.ActivityResultLauncher
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.core.content.FileProvider
import androidx.core.splashscreen.SplashScreen.Companion.installSplashScreen
import androidx.swiperefreshlayout.widget.SwipeRefreshLayout
import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * TapIn wraps the live web dashboard in a WebView so it behaves like a
 * dedicated app: it keeps the login session, supports camera capture and
 * file picking for profile photos / QR scanning, hands PDF/file downloads off to
 * the system Download Manager, and shows a proper offline screen instead of a blank page.
 *
 * Change HOME_URL below if the deployment URL ever changes.
 */
class MainActivity : AppCompatActivity() {

    companion object {
        private const val HOME_URL = "https://tapin-ispsctagudin.vercel.app/login.html"
    }

    private lateinit var webView: WebView
    private lateinit var swipeRefresh: SwipeRefreshLayout
    private lateinit var progressBar: ProgressBar
    private lateinit var offlineView: LinearLayout
    private lateinit var splashOverlay: LinearLayout

    private var filePathCallback: ValueCallback<Array<Uri>>? = null
    private var cameraImageUri: Uri? = null
    private var pendingWebPermissionRequest: PermissionRequest? = null

    private val fileChooserLauncher: ActivityResultLauncher<Intent> =
        registerForActivityResult(ActivityResultContracts.StartActivityForResult()) { result ->
            if (filePathCallback == null) return@registerForActivityResult

            var results: Array<Uri>? = null

            if (result.resultCode == RESULT_OK) {
                val data = result.data
                if (data?.data != null) {
                    results = arrayOf(data.data!!)
                } else {
                    val parsed = WebChromeClient.FileChooserParams.parseResult(result.resultCode, data)
                    if (!parsed.isNullOrEmpty()) {
                        results = parsed
                    } else if (cameraImageUri != null) {
                        results = arrayOf(cameraImageUri!!)
                    }
                }
            }

            filePathCallback?.onReceiveValue(results)
            filePathCallback = null
            cameraImageUri = null
        }

    private val permissionsLauncher: ActivityResultLauncher<Array<String>> =
        registerForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) { permissions ->
            pendingWebPermissionRequest?.let { request ->
                val allGranted = request.resources.all { res ->
                    when (res) {
                        PermissionRequest.RESOURCE_VIDEO_CAPTURE ->
                            (permissions[Manifest.permission.CAMERA] == true) ||
                                    (ContextCompat.checkSelfPermission(this, Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED)
                        PermissionRequest.RESOURCE_AUDIO_CAPTURE ->
                            (permissions[Manifest.permission.RECORD_AUDIO] == true) ||
                                    (ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED)
                        else -> true
                    }
                }

                if (allGranted) {
                    runOnUiThread { request.grant(request.resources) }
                } else {
                    runOnUiThread { request.deny() }
                }
                pendingWebPermissionRequest = null
            }
        }

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        installSplashScreen()
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        webView = findViewById(R.id.webView)
        swipeRefresh = findViewById(R.id.swipeRefresh)
        progressBar = findViewById(R.id.progressBar)
        offlineView = findViewById(R.id.offlineView)
        splashOverlay = findViewById(R.id.splashOverlay)
        val retryButton = findViewById<Button>(R.id.retryButton)

        setupWebView()

        retryButton.setOnClickListener { loadHome() }
        swipeRefresh.setOnRefreshListener { webView.reload() }

        if (savedInstanceState != null) {
            webView.restoreState(savedInstanceState)
        } else {
            loadHome()
        }

        onBackPressedDispatcher.addCallback(this) {
            if (webView.canGoBack()) {
                webView.goBack()
            } else {
                isEnabled = false
                onBackPressedDispatcher.onBackPressed()
            }
        }
    }

    @SuppressLint("SetJavaScriptEnabled")
    private fun setupWebView() {
        val settings: WebSettings = webView.settings
        settings.javaScriptEnabled = true
        settings.domStorageEnabled = true          // needed for localStorage (login token)
        settings.databaseEnabled = true
        settings.loadWithOverviewMode = true
        settings.useWideViewPort = true
        settings.cacheMode = WebSettings.LOAD_DEFAULT
        settings.mixedContentMode = WebSettings.MIXED_CONTENT_NEVER_ALLOW
        settings.setSupportZoom(false)
        settings.builtInZoomControls = false
        settings.mediaPlaybackRequiresUserGesture = false

        // Keep the session/login cookies across app restarts.
        CookieManager.getInstance().setAcceptCookie(true)
        CookieManager.getInstance().setAcceptThirdPartyCookies(webView, true)

        webView.webViewClient = object : WebViewClient() {
            override fun shouldOverrideUrlLoading(
                view: WebView,
                request: WebResourceRequest
            ): Boolean {
                // Keep everything inside the app instead of bouncing to Chrome.
                return false
            }

            override fun onPageStarted(view: WebView, url: String?, favicon: Bitmap?) {
                super.onPageStarted(view, url, favicon)
                progressBar.visibility = View.VISIBLE
            }

            override fun onPageFinished(view: WebView, url: String?) {
                super.onPageFinished(view, url)
                progressBar.visibility = View.GONE
                swipeRefresh.isRefreshing = false
                offlineView.visibility = View.GONE
                swipeRefresh.visibility = View.VISIBLE
                hideSplashOverlay()
            }

            override fun onReceivedError(
                view: WebView,
                request: WebResourceRequest,
                error: WebResourceError
            ) {
                super.onReceivedError(view, request, error)
                // Only treat a failure of the main page (not a sub-resource) as "offline".
                if (request.isForMainFrame) {
                    showOffline()
                }
            }
        }

        webView.webChromeClient = object : WebChromeClient() {
            override fun onProgressChanged(view: WebView, newProgress: Int) {
                super.onProgressChanged(view, newProgress)
                progressBar.progress = newProgress
                if (newProgress >= 100) progressBar.visibility = View.GONE
            }

            // Lets <input type="file"> open the camera capture or system file chooser.
            override fun onShowFileChooser(
                webView: WebView,
                callback: ValueCallback<Array<Uri>>,
                params: FileChooserParams
            ): Boolean {
                filePathCallback?.onReceiveValue(null)
                filePathCallback = callback

                val takePictureIntent = Intent(MediaStore.ACTION_IMAGE_CAPTURE)
                var photoUri: Uri? = null
                if (takePictureIntent.resolveActivity(packageManager) != null) {
                    photoUri = createCameraImageUri()
                    if (photoUri != null) {
                        takePictureIntent.putExtra(MediaStore.EXTRA_OUTPUT, photoUri)
                        cameraImageUri = photoUri
                    }
                }

                val contentSelectionIntent = params.createIntent()
                val chooserIntent = Intent(Intent.ACTION_CHOOSER).apply {
                    putExtra(Intent.EXTRA_INTENT, contentSelectionIntent)
                    putExtra(Intent.EXTRA_TITLE, "Select Action")
                    if (photoUri != null) {
                        putExtra(Intent.EXTRA_INITIAL_INTENTS, arrayOf(takePictureIntent))
                    }
                }

                return try {
                    fileChooserLauncher.launch(chooserIntent)
                    true
                } catch (_: Exception) {
                    filePathCallback?.onReceiveValue(null)
                    filePathCallback = null
                    cameraImageUri = null
                    false
                }
            }

            // Grants web permission requests (e.g., getUserMedia for camera/mic streams)
            // after ensuring runtime permissions are granted by Android OS.
            override fun onPermissionRequest(request: PermissionRequest) {
                val resources = request.resources
                val needsCamera = resources.contains(PermissionRequest.RESOURCE_VIDEO_CAPTURE)
                val needsAudio = resources.contains(PermissionRequest.RESOURCE_AUDIO_CAPTURE)

                val cameraGranted = (!needsCamera) || (ContextCompat.checkSelfPermission(
                    this@MainActivity,
                    Manifest.permission.CAMERA
                ) == PackageManager.PERMISSION_GRANTED)

                val audioGranted = (!needsAudio) || (ContextCompat.checkSelfPermission(
                    this@MainActivity,
                    Manifest.permission.RECORD_AUDIO
                ) == PackageManager.PERMISSION_GRANTED)

                if (cameraGranted && audioGranted) {
                    runOnUiThread { request.grant(resources) }
                } else {
                    pendingWebPermissionRequest = request
                    val permissionsToRequest = mutableListOf<String>()
                    if (needsCamera && !cameraGranted) {
                        permissionsToRequest.add(Manifest.permission.CAMERA)
                    }
                    if (needsAudio && !audioGranted) {
                        permissionsToRequest.add(Manifest.permission.RECORD_AUDIO)
                    }
                    permissionsLauncher.launch(permissionsToRequest.toTypedArray())
                }
            }
        }

        // Hand PDF/report downloads off to the system Download Manager
        // instead of trying to render them inline.
        webView.setDownloadListener { url, userAgent, contentDisposition, mimeType, _ ->
            try {
                val request = DownloadManager.Request(Uri.parse(url))
                request.setMimeType(mimeType)
                request.addRequestHeader("cookie", CookieManager.getInstance().getCookie(url))
                request.addRequestHeader("User-Agent", userAgent)
                request.setDescription("Downloading file...")
                request.setTitle(URLUtil.guessFileName(url, contentDisposition, mimeType))
                request.setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED)
                request.setDestinationInExternalPublicDir(
                    Environment.DIRECTORY_DOWNLOADS,
                    URLUtil.guessFileName(url, contentDisposition, mimeType)
                )
                val dm = getSystemService(DOWNLOAD_SERVICE) as DownloadManager
                dm.enqueue(request)
            } catch (_: Exception) {
                // If the download can't be handed off, at least open it in the WebView.
                webView.loadUrl(url)
            }
        }
    }

    private fun createCameraImageUri(): Uri? {
        return try {
            val timeStamp = SimpleDateFormat("yyyyMMdd_HHmmss", Locale.US).format(Date())
            val imageFileName = "JPEG_${timeStamp}_"
            val imageFile = File.createTempFile(imageFileName, ".jpg", cacheDir)
            FileProvider.getUriForFile(
                this,
                "${applicationContext.packageName}.fileprovider",
                imageFile
            )
        } catch (_: Exception) {
            null
        }
    }

    private fun loadHome() {
        if (isOnline()) {
            offlineView.visibility = View.GONE
            swipeRefresh.visibility = View.VISIBLE
            webView.loadUrl(HOME_URL)
        } else {
            showOffline()
        }
    }

    private fun hideSplashOverlay() {
        if (splashOverlay.visibility == View.VISIBLE) {
            splashOverlay.animate()
                .alpha(0f)
                .setDuration(350)
                .withEndAction {
                    splashOverlay.visibility = View.GONE
                    splashOverlay.alpha = 1f
                }
        }
    }

    private fun showOffline() {
        progressBar.visibility = View.GONE
        swipeRefresh.isRefreshing = false
        swipeRefresh.visibility = View.GONE
        splashOverlay.visibility = View.GONE
        offlineView.visibility = View.VISIBLE
    }

    private fun isOnline(): Boolean {
        val cm = getSystemService(CONNECTIVITY_SERVICE) as ConnectivityManager
        val network = cm.activeNetwork ?: return false
        val capabilities = cm.getNetworkCapabilities(network) ?: return false
        return capabilities.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET)
    }

    override fun onSaveInstanceState(outState: Bundle) {
        super.onSaveInstanceState(outState)
        webView.saveState(outState)
    }
}
