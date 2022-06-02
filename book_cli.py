import argparse
import sys

from book_apis import GoodreadsApi, GoogleBooksApi, OpenLibraryApi

def main():
  parser = argparse.ArgumentParser()

  parser.add_argument("api", choices=["google_books", "open_library", "goodreads"])
  parser.add_argument("--google_books_api_key", default="data/books_api")
  subparsers = parser.add_subparsers(required=True, dest="command")

  title_parser = subparsers.add_parser("title")
  title_parser.add_argument("title", nargs="+")
  title_parser.add_argument("-by", dest="author", nargs="+", required=True)

  isbn_parser = subparsers.add_parser("isbn")
  isbn_parser.add_argument("isbn", type=int)

  link_parser = subparsers.add_parser("link")
  link_parser.add_argument("isbn", type=int)

  thumbnail_parser = subparsers.add_parser("thumbnail")
  thumbnail_parser.add_argument("isbn", type=int)

  parser.add_argument("-v", "--verbose", dest="verbose", action="store_true")
  args = parser.parse_args()

  match args.api:
    case "google_books":
      with open(args.google_books_api_key, "r") as f:
        key = f.read().strip()
      book_api = GoogleBooksApi(key, args.verbose)
    case "open_library":
      book_api = OpenLibraryApi(args.verbose)
    case "goodreads":
      book_api = GoodreadsApi(args.verbose)
    case _:
      sys.exit(f"Unrecognized api: {args.api}")

  match args.command:
    case "title":
      print(book_api.search_author_title(args.author, args.title))
    case "isbn":
      print(book_api.search_isbn(args.isbn))
    case "link":
      print(book_api.link_from_isbn(args.isbn))
    case "thumbnail":
      print(book_api.thumbnail_from_isbn(args.isbn))


if __name__ == "__main__":
  main()
