import os
import sys

# Add server directory to path so we can import dependencies if needed
sys.path.append(os.path.join(os.path.dirname(__file__), "..", "server"))

import ebooklib
from ebooklib import epub

def test_read_write(filepath):
    print(f"Testing {filepath}")
    try:
        book = epub.read_epub(filepath, options={'ignore_ncx': True})
        print(f"Read successful. Title: {book.get_metadata('DC', 'title')}")
        
        # Try to set a title
        book.set_unique_metadata('DC', 'title', 'Test Title')
        
        # Try to write
        out_path = filepath + ".test_out.epub"
        epub.write_epub(out_path, book)
        print(f"Write successful to {out_path}")
        
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    if len(sys.argv) > 1:
        test_read_write(sys.argv[1])
    else:
        print("Please provide a path to an epub file")
