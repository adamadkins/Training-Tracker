import UIKit
import Capacitor

class ViewController: CAPBridgeViewController {

    override func viewDidLoad() {
        super.viewDidLoad()

        // Dark background matching #0b0f1a — prevents white flash between page navigations
        let bg = UIColor(red: 0.043, green: 0.059, blue: 0.102, alpha: 1.0)
        view.backgroundColor = bg

        if let wv = webView {
            wv.isOpaque = false
            wv.backgroundColor = bg
            wv.scrollView.backgroundColor = bg
        }
    }
}
