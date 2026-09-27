import { describe, it, expect } from 'vitest'
import EpubCFI from 'epubjs/src/epubcfi'
import { pageListEntries, printListAt, fragmentBeforeOrAt } from './printPages'

describe('printPages (issue #730, print-page-list mode)', () => {
    describe('printListAt', () => {
        it('picks the last qualifying label across sections', () => {
            const entries = [
                { sectionIndex: 1, fragment: 'a', label: '1' },
                { sectionIndex: 1, fragment: 'b', label: '2' },
                { sectionIndex: 3, fragment: 'c', label: '3' },
            ]
            const result = printListAt(entries, 3, () => false)
            expect(result).toEqual({ currentLabel: '2', firstLabel: '1', lastLabel: '3' })
        })

        it('includes a same-section entry when isBeforeOrAt is true', () => {
            const entries = [
                { sectionIndex: 1, fragment: 'a', label: '1' },
                { sectionIndex: 1, fragment: 'b', label: '2' },
                { sectionIndex: 3, fragment: 'c', label: '3' },
            ]
            const result = printListAt(entries, 3, () => true)
            expect(result).toEqual({ currentLabel: '3', firstLabel: '1', lastLabel: '3' })
        })

        it('returns a null currentLabel when nothing before or at the position qualifies', () => {
            const entries = [
                { sectionIndex: 1, fragment: 'a', label: '1' },
                { sectionIndex: 1, fragment: 'b', label: '2' },
                { sectionIndex: 3, fragment: 'c', label: '3' },
            ]
            const result = printListAt(entries, 0, () => false)
            expect(result).toEqual({ currentLabel: null, firstLabel: '1', lastLabel: '3' })
        })

        it('returns null for an empty entry list', () => {
            expect(printListAt([], 0, () => false)).toBeNull()
        })
    })

    describe('pageListEntries', () => {
        const spineHrefs = ['OEBPS/chapter1.xhtml', 'OEBPS/chapter2.xhtml', 'OEBPS/chapter3.xhtml']

        it('drops entries whose page is not finite and maps hrefs (with fragments) to spine indices', () => {
            const book = {
                pageList: {
                    pageList: [
                        { href: 'OEBPS/chapter1.xhtml#p1', page: 1 },
                        { href: 'OEBPS/chapter1.xhtml#pxiii', page: NaN }, // roman numeral -> NaN, dropped
                        { href: 'OEBPS/chapter2.xhtml#p2', page: 2 },
                    ],
                },
            }
            const entries = pageListEntries(book, spineHrefs)
            expect(entries).toEqual([
                { sectionIndex: 0, fragment: 'p1', label: '1' },
                { sectionIndex: 1, fragment: 'p2', label: '2' },
            ])
        })

        it('matches hrefs by suffix, in either direction, against the spine hrefs', () => {
            const book = {
                pageList: {
                    pageList: [
                        { href: '/full/path/OEBPS/chapter3.xhtml#end', page: 9 },
                    ],
                },
            }
            const entries = pageListEntries(book, spineHrefs)
            expect(entries).toEqual([{ sectionIndex: 2, fragment: 'end', label: '9' }])
        })

        it('drops an entry whose href does not map to any spine item', () => {
            const book = { pageList: { pageList: [{ href: 'nowhere.xhtml#x', page: 1 }] } }
            expect(pageListEntries(book, spineHrefs)).toEqual([])
        })

        it('handles a missing page list', () => {
            expect(pageListEntries({}, spineHrefs)).toEqual([])
            expect(pageListEntries({ pageList: {} }, spineHrefs)).toEqual([])
        })

        it('drops an entry with a non-string href', () => {
            const book = { pageList: { pageList: [{ page: 1 }] } }
            expect(pageListEntries(book, spineHrefs)).toEqual([])
        })

        it('keeps an entry with no fragment', () => {
            const book = { pageList: { pageList: [{ href: 'OEBPS/chapter1.xhtml', page: 1 }] } }
            expect(pageListEntries(book, spineHrefs)).toEqual([{ sectionIndex: 0, fragment: '', label: '1' }])
        })
    })

    describe('fragmentBeforeOrAt', () => {
        // Build a real element + CFI with jsdom and the actual epub.js CFI code,
        // per the task brief's preference for the real implementation over a stub.
        function makeSection(cfiForElement) {
            return { cfiFromElement: () => cfiForElement }
        }

        it('is true when the marker element resolves at or before the start CFI', () => {
            const doc = document.implementation.createHTMLDocument('t')
            const p = doc.createElement('p')
            p.id = 'mark'
            doc.body.appendChild(p)
            const contents = { document: doc }

            const startCfi = 'epubcfi(/6/4[chap01ref]!/4/10/2/1:5)'
            // An element CFI that is earlier in the same chapter than startCfi.
            const earlierCfi = 'epubcfi(/6/4[chap01ref]!/4/2/1:0)'
            const section = makeSection(earlierCfi)

            expect(fragmentBeforeOrAt(contents, 'mark', startCfi, section)).toBe(true)
            expect(new EpubCFI().compare(earlierCfi, startCfi)).toBeLessThanOrEqual(0)
        })

        it('is false when the marker element resolves after the start CFI', () => {
            const doc = document.implementation.createHTMLDocument('t')
            const p = doc.createElement('p')
            p.id = 'mark'
            doc.body.appendChild(p)
            const contents = { document: doc }

            const startCfi = 'epubcfi(/6/4[chap01ref]!/4/2/1:0)'
            const laterCfi = 'epubcfi(/6/4[chap01ref]!/4/10/2/1:5)'
            const section = makeSection(laterCfi)

            expect(fragmentBeforeOrAt(contents, 'mark', startCfi, section)).toBe(false)
        })

        it('is false when the element is missing from the document', () => {
            const doc = document.implementation.createHTMLDocument('t')
            const contents = { document: doc }
            const section = makeSection('epubcfi(/6/4[chap01ref]!/4/2/1:0)')

            expect(fragmentBeforeOrAt(contents, 'missing', 'epubcfi(/6/4[chap01ref]!/4/10/2/1:5)', section)).toBe(false)
        })

        it('is false when contents has no document', () => {
            const section = makeSection('epubcfi(/6/4[chap01ref]!/4/2/1:0)')
            expect(fragmentBeforeOrAt({}, 'mark', 'epubcfi(/6/4[chap01ref]!/4/10/2/1:5)', section)).toBe(false)
        })
    })
})
