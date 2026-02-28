import UIKit
import Capacitor

class ViewController: CAPBridgeViewController {

    override func viewDidLoad() {
        super.viewDidLoad()

        // Theme-aware background: match the user's system appearance so the
        // brief flash between page navigations blends with the current theme
        let bg = UIColor { trait in
            trait.userInterfaceStyle == .dark
                ? UIColor(red: 0.043, green: 0.059, blue: 0.102, alpha: 1.0)   // #0b0f1a
                : UIColor(red: 0.973, green: 0.980, blue: 0.988, alpha: 1.0)   // #f8fafc
        }
        view.backgroundColor = bg

        if let wv = webView {
            wv.isOpaque = false
            wv.backgroundColor = bg
            wv.scrollView.backgroundColor = bg
            // Hide scrollbar for native app feel
            wv.scrollView.showsVerticalScrollIndicator = false
            wv.scrollView.showsHorizontalScrollIndicator = false
        }
    }
}
