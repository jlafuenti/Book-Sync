import React from 'react'
import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import fs from 'node:fs'
import path from 'node:path'
import TermsPage from './TermsPage'

const renderPage = () =>
    render(
        <MemoryRouter>
            <TermsPage />
        </MemoryRouter>,
    )

describe('TermsPage (issue #262)', () => {
    it('says the service is best-effort with no warranty or uptime guarantee', () => {
        renderPage()
        expect(screen.getByTestId('terms-no-warranty').textContent).toMatch(/no warranty/i)
        expect(screen.getByTestId('terms-no-warranty').textContent).toMatch(/uptime/i)
    })

    it('carries the sentence the DRM import path needs', () => {
        // import-sources.md's ACSM/Audible pipelines exist so a user can read
        // their own purchases. This is the only place that is written down for
        // the person creating the account, so it must not quietly disappear.
        renderPage()
        expect(screen.getByTestId('terms-your-content').textContent).toMatch(
            /legally entitled to hold/i,
        )
    })

    it('says the operator may remove accounts', () => {
        renderPage()
        expect(screen.getByTestId('terms-accounts').textContent).toMatch(/remove your account/i)
    })

    it('defers data handling to the privacy policy', () => {
        renderPage()
        expect(screen.getByTestId('terms-data').textContent).toMatch(/privacy policy/i)
    })

    it('offers a contact route for takedown and deletion requests', () => {
        renderPage()
        expect(screen.getByTestId('terms-contact')).toBeTruthy()
    })

    it('links back to the sign-in page', () => {
        renderPage()
        expect(screen.getByRole('link', { name: /sign in/i })).toBeTruthy()
    })

    it('reads nothing from the API — it must render with no session', async () => {
        // Same rule as AccountDeletionPage (#146): this page renders before the
        // auth gate, so an import of the API client would fire requests that can
        // only 401, and a future refactor could make it require a token.
        const source = fs.readFileSync(
            path.join(__dirname, 'TermsPage.jsx'),
            'utf8',
        )
        expect(source).not.toMatch(/from '\.\.\/api'/)
    })
})
