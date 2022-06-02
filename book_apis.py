import json
import models
import requests
import traceback


from pprint import pprint


class BaseApi:
  def link_from_isbn(self, isbn):
    raise NotImplementedError("link_from_isbn() is unimplemented.")

  def thumbnail_from_isbn(self, isbn):
    raise NotImplementedError("thumbnail_from_isbn() is unimplemented.")

  def search_author_title(self, author: str, title: str) -> list[models.Book]:
    raise NotImplementedError("search_author_title() is unimplemented.")

  def search_isbn(self, isbn: int) -> models.Book:
    raise NotImplementedError("search_isbn() is unimplemented.")


class GoodreadsApi(BaseApi):
  def __init__(self, verbose):
    self.verbose = verbose

  def link_from_isbn(self, isbn):
    url = "https://www.goodreads.com/search"
    params = {"q": isbn, "ref": "nav_sb_noss_l_13"}
    r = requests.get(url, params, allow_redirects=False)
    if self.verbose:
      print(f"{r}")
      print(f"{r.text}")
    if r.status_code == 302:
      return r.headers["Location"]

    print(f"GET {r.request.url} returned unexpected status code: {r.status_code}.")
    return None


class OpenLibraryApi(BaseApi):
  def __init__(self, verbose):
    self.verbose = verbose

  def link_from_isbn(self, isbn):
    return f"https://openlibrary.org/isbn/{isbn}"

  def thumbnail_from_isbn(self, isbn):
    return f"https://covers.openlibrary.org/b/isbn/{isbn}-M.jpg"

  def search_author_title(self, author: str, title: str) -> list[models.Book]:
    url = f"http://openlibrary.org/search.json"
    params = {"author": author, "title": title}
    r = requests.get(url, params)

    data = r.json()
    if self.verbose:
      print(json.dumps(data, indent=4))

    books = []
    for doc in data["docs"]:
      try:
        book = models.Book()

        book.title = doc["title"]
        book.author = ", ".join(doc["author_name"])

        book.isbn, = doc["isbn"]

        book.open_library_url = f"https://openlibrary.org{doc['key']}"
        book.goodreads_url = self.__find_goodreads_url(doc, book.isbn)
        thumbnail_url = self.thumbnail_from_isbn(isbn)

        books.append(book)
      except (KeyError, IndexError, ValueError) as e:
        print(f"Ignoring entry for {title} by {author}:\n{traceback.format_exc()}")

    return books

  def search_isbn(self, isbn: int) -> models.Book:
    url = f"https://openlibrary.org/api/books"
    key = f"ISBN:{isbn}"
    params = {"bibkeys": key, "jscmd": "details", "format": "json"}
    r = requests.get(url, params)

    data = r.json()
    if self.verbose:
      print(json.dumps(data, indent=4))

    if not data or key not in data:
      raise ValueError(f"No book found for ISBN {isbn}.")

    details = data[key]["details"]

    book = models.Book()
    book.author = ", ".join(author["name"] for author in details["authors"])
    book.title = details["title"]
    book.isbn = isbn

    book.open_library_url = f'https://openlibrary.org{details["key"]}'
    book.goodreads_url = self.__find_goodreads_url(details, isbn)
    book.thumbnail_url = self.thumbnail_from_isbn(isbn)

    return book


  def __find_goodreads_url(self, data, isbn):
    # find_by_author_title case: data is a doc
    try:
      goodreads_id = data["id_goodreads"][0]
    except:
      pass

    # find_by_isbn case: data is the details
    try:
      goodreads_id = data["identifiers"]["goodreads"][0]
    except:
      pass

    if goodreads_id is not None:
      return f"https://www.goodreads.com/book/show/{goodreads_id}"

    # Last ditched effort, try querying Goodreads with the ISBN.
    return GoodreadsApi(self.verbose).link_from_isbn(isbn)


class GoogleBooksApi(BaseApi):
  def __init__(self, key, verbose, max_results=10):
    self.key = key
    self.verbose = verbose
    self.max_results = max_results

  def thumbnail_from_isbn(self, isbn):
    url = "https://www.googleapis.com/books/v1/volumes"
    params = {
        "q": f"isbn:{isbn}",
        "maxResults": {self.max_results},
        "printType": "books",
        "orderBy": "relevance"
    }
    r = requests.get(url, params)
    data = r.json()
    try:
      image_links = data["items"][0]["volumeInfo"]["imageLinks"]
      return image_links.get("thumbnail", image_links.get("smallThumbnail", None))
    except:
      print(f"No thumbnail found for {isbn}")
      return None



  def search_author_title(self, author, title):
    url = "https://www.googleapis.com/books/v1/volumes"
    params = {
        "q": f"intitle:{title} inauthor:{author}",
        "maxResults": {self.max_results},
        "printType": "books",
        "orderBy": "relevance"
    }
    r = requests.get(url, params)
    data = r.json()
    if self.verbose:
      pprint(data)

    return self.__parse_response(data)

  def __parse_response(self, data) -> list[models.Book]:
    if data["totalItems"] == 0 or "items" not in data:
      return []

    books = []
    for item in data["items"]:
      if self.verbose:
        pprint(item)

      volume_info = item["volumeInfo"]
      if volume_info["language"] != "en":
        continue

      book = models.Book()
      book.title = volume_info["title"]
      if "authors" not in volume_info:
        print(f"Skipping entry for {book.title} with no 'authors' key.")
        continue
      book.author = ",".join(volume_info["authors"])

      isbn = None
      for d in volume_info["industryIdentifiers"]:
        if d["type"] == "ISBN_13":
          isbn = d["identifier"]
        elif isbn is None and d["type"] == "ISBN_10":
          isbn = d["identifier"]

      if isbn is None:
        print(f"Unable to find ISBN for {book.title} by {book.author}. Skipping...")
        continue
      book.isbn = isbn

      if "imageLinks" in volume_info:
        image_links = volume_info["imageLinks"]
        book.thumbnail_url = image_links.get("thumbnail", None) or image_links.get("smallThumbnail", None)

      url = item["selfLink"]
      date = volume_info["publishedDate"]

      books.append(book)

    return books
