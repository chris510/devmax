import Foundation

/// External study material is data, not trusted navigation. Only ordinary web
/// links become tappable; malformed values and executable/file schemes remain
/// visible as unavailable metadata instead of becoming dead or unsafe actions.
enum SafeExternalURL {
    static func parse(_ value: String) -> URL? {
        guard let url = URL(string: value),
              let scheme = url.scheme?.lowercased(),
              scheme == "http" || scheme == "https",
              url.host != nil
        else { return nil }
        return url
    }
}
