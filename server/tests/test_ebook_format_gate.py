"""
Alignment only ever runs on the artifact the readers render (issue #101).

Both readers — Readium on Android, epub.js on the web — render EPUB and nothing
else. The alignment pipeline, though, parsed whatever file the pair pointed at,
and `extract_mobi_sentences` flattens a whole `.mobi` into a *single* chapter.
A sync map built that way names chapters that cannot exist in the EPUB the reader
shows, so every position resolved through it lands on the wrong axis and has to
fall back to the text-preview / percent rungs of the restore ladder.

The gate lives in `check_ebook_integrity`, which the queue worker already runs at
1.5% progress — so a `.mobi` pair now fails in seconds with an instruction,
instead of after a multi-hour transcription with a useless map.
"""

from services.ebook_integrity import check_ebook_integrity

from tests.factories import make_book_pair


class TestFormatGate:
    def test_mobi_is_rejected_before_parsing(self, monkeypatch, tmp_path):
        """A .mobi never reaches the parser: the format alone decides it."""
        def _boom(_path):
            raise AssertionError("extract_book_sentences must not run on a .mobi")

        monkeypatch.setattr(
            "services.epub_parser.extract_book_sentences", _boom
        )
        mobi = tmp_path / "book.mobi"
        mobi.write_bytes(b"not really a mobi")

        ok, detail = check_ebook_integrity(str(mobi))

        assert ok is False
        assert ".mobi" in detail
        assert "convert" in detail.lower()

    def test_azw3_is_rejected_too(self, tmp_path):
        azw3 = tmp_path / "book.azw3"
        azw3.write_bytes(b"x")
        ok, detail = check_ebook_integrity(str(azw3))
        assert ok is False
        assert "convert" in detail.lower()

    def test_pdf_is_rejected_with_the_same_gate(self, tmp_path):
        """A PDF used to reach the EPUB parser and die on a zip error."""
        pdf = tmp_path / "book.pdf"
        pdf.write_bytes(b"%PDF-1.4")
        ok, detail = check_ebook_integrity(str(pdf))
        assert ok is False
        assert "epub" in detail.lower()

    def test_epub_still_reaches_the_extraction_stage(self, monkeypatch, tmp_path):
        """The gate is about format only — a real EPUB proceeds as before."""
        import zipfile

        from services.epub_parser import EpubSentence

        epub = tmp_path / "book.epub"
        with zipfile.ZipFile(epub, "w") as z:
            z.writestr("mimetype", "application/epub+zip")

        sentences = [
            EpubSentence(chapter=0, sentence_index=i, text=f"sentence {i}")
            for i in range(60)
        ]
        monkeypatch.setattr(
            "services.epub_parser.extract_book_sentences", lambda _p: sentences
        )

        ok, detail = check_ebook_integrity(str(epub))

        assert ok is True
        assert "60" in detail


class TestRealignEndpointGate:
    async def test_realign_refuses_a_mobi_backed_pair(
        self, db, make_client, make_user, auth_header
    ):
        from models.book import EBook
        from routers import transcription

        pair = await make_book_pair(db)
        ebook = await db.get(EBook, pair.ebook_id)
        ebook.filename = "book.mobi"
        ebook.file_path = "/x/book.mobi"
        await db.commit()

        user = await make_user(username="editor", role="admin")
        async with make_client(transcription.router) as c:
            r = await c.post(
                f"/api/transcription/{pair.id}/realign", headers=auth_header(user)
            )

        assert r.status_code == 422
        assert "convert" in r.json()["detail"].lower()

    async def test_realign_on_an_epub_pair_rebuilds_the_map(
        self, db, monkeypatch, make_client, make_user, auth_header
    ):
        """The happy path still works through the extracted service."""
        import json

        from models.transcript import AudioTranscript
        from routers import transcription
        from services.epub_parser import EpubSentence

        pair = await make_book_pair(db)
        db.add(AudioTranscript(
            pair_id=pair.id,
            audiobook_path="/x/a.m4b",
            sentence_count=2,
            sentences_json=json.dumps([
                {"text": "chapter one opening line", "start_ms": 0, "end_ms": 3000},
                {"text": "chapter two begins now", "start_ms": 9000, "end_ms": 12000},
            ]),
        ))
        await db.commit()
        pair_id = pair.id

        monkeypatch.setattr(
            "services.realign.extract_book_sentences",
            lambda _p: [
                EpubSentence(chapter=0, sentence_index=0, text="chapter one opening line"),
                EpubSentence(chapter=1, sentence_index=0, text="chapter two begins now"),
            ],
        )
        user = await make_user(username="editor2", role="admin")
        async with make_client(transcription.router) as c:
            r = await c.post(
                f"/api/transcription/{pair_id}/realign", headers=auth_header(user)
            )

        assert r.status_code == 200, r.text
        body = r.json()
        assert body["points"] == 2
        assert body["matched"] + body["interpolated"] == body["points"]

    async def test_realign_needs_a_transcript(
        self, db, make_client, make_user, auth_header
    ):
        from routers import transcription

        pair = await make_book_pair(db)
        user = await make_user(username="editor3", role="admin")
        async with make_client(transcription.router) as c:
            r = await c.post(
                f"/api/transcription/{pair.id}/realign", headers=auth_header(user)
            )

        assert r.status_code == 404
        assert "transcript" in r.json()["detail"].lower()
