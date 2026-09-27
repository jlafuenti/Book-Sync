import Modal from '../components/Modal'

/**
 * The one-time offer to take the guided walkthrough (issue #598, Track A).
 * Shown by `TourProvider` when `user.web_tour_offered_at` is `null`. Reuses
 * `Modal` for the focus trap / Escape / backdrop behaviour every other
 * dialog in the app already gets (issue #279) — "Not now" and Escape are
 * equivalent here, both just decline.
 */
export default function TourOffer({ onNotNow, onTakeTour }) {
    return (
        <Modal labelledBy="tour-offer-title" onClose={onNotNow}>
            <h2 id="tour-offer-title" style={{ marginTop: 0 }}>Take the 5-minute tour?</h2>
            <p style={{ color: 'var(--text-muted)' }}>
                A guided walkthrough of Tandem, using your own library. Quit any time.
            </p>
            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8 }}>
                <button className="btn btn-secondary" onClick={onNotNow}>Not now</button>
                <button className="btn btn-primary" onClick={onTakeTour}>Take the tour</button>
            </div>
        </Modal>
    )
}
