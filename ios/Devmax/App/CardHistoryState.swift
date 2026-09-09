import Combine
import Foundation

@MainActor
final class CardHistoryState: ObservableObject {
    enum Phase {
        case loading
        case ready(CardDetail)
        case failed
    }

    @Published private(set) var phase: Phase = .loading
    private var loadID = UUID()

    func load(cardID: UUID, api: DevmaxAPI) async {
        let requestID = UUID()
        loadID = requestID
        phase = .loading
        do {
            let detail = try await api.card(cardID)
            guard loadID == requestID, !Task.isCancelled else { return }
            phase = .ready(detail)
        } catch {
            guard loadID == requestID, !Task.isCancelled else { return }
            phase = .failed
        }
    }
}
