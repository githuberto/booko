import argparse
import sqlalchemy
import enum


from sqlalchemy import orm, Column, ForeignKey, Integer, String, Enum, select


Base = orm.declarative_base()


class Shelf(enum.Enum):
  RECOMMENDED = 1
  READ = 2
  SMUT = 3


class Rating(Base):
  __tablename__ = "ratings"

  id = Column(Integer, primary_key=True)
  user_id = Column(Integer)
  book_id = Column(Integer, ForeignKey("books.id"))
  rating = Column(Integer)

  book = orm.relationship("Book", back_populates="ratings")

  def __repr__(self):
    d = {
        "id":  self.id,
        "user_id": self.user_id,
        "book_id": self.book_id,
        "rating": self.rating
    }
    return f"Rating{tuple(f'{k}={v}' for k, v in d.items())}"


class Book(Base):
  __tablename__ = "books"

  id = Column(Integer, primary_key=True)
  title = Column(String)
  author = Column(String)
  isbn = Column(String)
  open_library_url= Column(String)
  goodreads_url= Column(String)
  thumbnail_url = Column(String)
  shelf = Column(Enum(Shelf))
  message_id = Column(Integer)
  user_id = Column(Integer)

  ratings = orm.relationship("Rating", order_by=Rating.id, back_populates="book")

  def __repr__(self):
    d = {
        "id":  self.id,
        "title": self.title,
        "author": self.author,
        "isbn": self.isbn,
        "open_library_url": self.open_library_url,
        "goodreads_url": self.goodreads_url,
        "thumbnail_url": self.thumbnail_url,
        "shelf": self.shelf,
        "message_id": self.message_id,
    }
    return f"Book{tuple(f'{k}={v}' for k, v in d.items())}"


Session = None

def initialize(database):
  global Session

  engine = sqlalchemy.create_engine(f"sqlite:///{database}", future=True)
  Base.metadata.create_all(engine)
  Session = orm.sessionmaker(engine)


def main():
  initialize("data/test_alchemy.db")
  rat = None
  with Session() as s:
    stmt = sqlalchemy.select(Rating)
    for r in s.execute(stmt).scalars():
      r.rating += 1
      rat = r
    s.commit()

  # with Session() as s:
    # print(rat)


if __name__ == "__main__":
  main()
