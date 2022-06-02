import argparse
import asyncio
import config
import discord
import models
import traceback


from book_apis import GoodreadsApi, GoogleBooksApi, OpenLibraryApi
from discord import app_commands
from discord import ui
from discord.ext import commands
from emoji import emojize
from models import Shelf, Book, Rating
from sqlalchemy import select


# CONFIG = config.TEST_CONFIG
CONFIG = config.LIVE_CONFIG


DELAY = 2
NEXT_EMOJI = "⬇️"
PREVIOUS_EMOJI = "⬆️"
EDIT_EMOJI = "✏️"
CANCEL_EMOJI = "❌"
SUBMIT_EMOJI = "✅"
STAR_EMOJI = "⭐"
INVIS = "\u200b"


Session = None


def check_channel(channel_map):
  def predicate(interaction: discord.Interaction) -> bool:
    return interaction.channel.id in channel_map
  return app_commands.check(predicate)


def embed_from_book(book: Book, guild: discord.Guild):
  embed = discord.Embed(
      type="rich",
      colour=discord.Colour.random())
  # embed.set_footer(text=f"Use `/rate` to rate it!")
  desc_map = {
      "Title": f"*{book.title}*",
      "Author": book.author,
      "ISBN": book.isbn,
      "Goodreads": book.goodreads_url,
  }
  description = "\n".join(f"**{k}:** {v}" for k, v in desc_map.items())
  embed.description = description
  embed.set_thumbnail(url=book.thumbnail_url)

  # Add the user who recommended it, if available.
  if book.user_id:
    user = guild.get_member(book.user_id)
    if not user:
      print(f"Ignoring missing recommending user with id {book.user_id} for {book}")
    else:
      embed.set_author(name=f"{user.name}#{user.discriminator}", icon_url=user.avatar.url)

  # This should be fine for now, but could be accessed without a session in the
  # add book flow.
  if book.ratings:
    users = []
    stars = []
    for rating in book.ratings:
      user = guild.get_member(rating.user_id)
      if not user:
        print(f"Ignoring missing user with id {rating.user_id} for {book}")
        continue
      users.append(user.mention)
      stars.append(STAR_EMOJI*rating.rating)
    embed.add_field(name="User", value="\n".join(users))
    embed.add_field(name="Rating", value="\n".join(stars))

  return embed


class EditBookModal(ui.Modal):
  def __init__(self, book_choice, book):
    super().__init__(title="Manually edit book.")
    self.book_choice = book_choice

    self.add_item(ui.TextInput(label="Title", default=book.title))
    self.add_item(ui.TextInput(label="Author", default=book.author))
    self.add_item(ui.TextInput(label="ISBN", default=book.isbn))
    self.add_item(ui.TextInput(label="Goodreads", default=book.goodreads_url))
    self.add_item(ui.TextInput(label="Thumbnail", default=book.thumbnail_url))

    self.shelf = book.shelf

    self.book_response_queue = asyncio.Queue()

  async def await_submit(self) -> tuple[discord.Interaction, Book]:
    return await self.book_response_queue.get()

  async def on_submit(self, interaction: discord.Interaction):
    title, author, isbn, goodreads_url, thumbnail_url = self.children
    new_book = Book(
        title=title.value,
        author=author.value,
        isbn=isbn.value,
        goodreads_url=goodreads_url.value,
        thumbnail_url=thumbnail_url.value,
        shelf=self.shelf)
    # Place the interaction and the new book into the queue so that the view
    # which spawned this modal can update.
    await self.book_response_queue.put((interaction, new_book))

  async def on_timeout(self):
    # TODO: This might be better handled with asyncio.Condition.wait_for, but
    # it doesn't appear there are guarantees about the timing of
    # ui.Modal.is_finished (presumably after on_submit.
    self.book_response_queue.put(None)

  async def on_error(self, interaction: discord.Interaction, error: Exception):
    await interaction.response.send_message("Oops! Something went wrong editing your book.", ephemeral=True)
    traceback.print_exception(error)
    self.book_response_queue.put(None)


class RatingButton(ui.Button):
  def __init__(self, book_id, value, emoji):
    super().__init__(
        label=str(value),
        emoji=emoji,
        custom_id=f"rating_button_{book_id}_{value}")
    self.value = value

  async def callback(self, itx: discord.Interaction):
    await self.view.handle_rating(itx, self.value)


class FinalizedBook(ui.View):
  def __init__(self, book):
    super().__init__(timeout=None)

    self.book_id = book.id
    if self.book_id is None:
      with Session() as session:
        session.add(book)
        session.commit()
        # We can now store the book id since it has been generated.
        self.book_id = book.id

    # Only add ratings for past books.
    if book.shelf == Shelf.READ:
      emoji_names = (
          ":face_vomiting:",
          ":nauseated_face:",
          ":thinking_face:",
          ":slightly_smiling_face:",
          ":smiling_face_with_heart-eyes:"
      )
      emojis = map(emojize, emoji_names)
      for i, emoji in enumerate(emojis, 1):
        self.add_item(RatingButton(self.book_id, i, emoji))

  async def send_message(self, itx: discord.Interaction):
    with Session() as session:
      stmt = select(Book).where(Book.id == self.book_id)
      book = session.execute(stmt).scalar()
      embed = embed_from_book(book, itx.guild)

      # If the book doesn't have an id, that means this is the first time we're
      # sending it. Use send_message(), and store the id.
      # ip = f"is_persistent(): {self.is_persistent()}"
      if book.message_id is None:
        message = await itx.channel.send(embed=embed, view=self)
        book.message_id = message.id
        session.commit()
        await itx.response.defer()
        await asyncio.sleep(1)
        await itx.delete_original_message()
      else:
        # Otherwise, just edit the existing message.
        await itx.response.edit_message(embed=embed, view=self)

  async def handle_rating(self, itx: discord.Interaction, value: int):
    user = itx.user
    with Session() as session:
      stmt = select(Rating).where(Rating.user_id == user.id).where(Rating.book_id == self.book_id)
      rating = session.execute(stmt).scalar()
      if not rating:
        # Create a new rating if there isn't an existing.
        rating = Rating(user_id=user.id, book_id=self.book_id)
        session.add(rating)

      # Delete the rating if it's the same as a prior, add the rating otherwise.
      if rating.rating == value:
        session.delete(rating)
      else:
        rating.rating = value
      session.commit()
    await self.send_message(itx)


class BookChoice(ui.View):
  def __init__(self, bot, books, original_message):
    super().__init__(timeout=None)
    self.books = books
    self.i = 0
    self.original_message = original_message
    self.view_message = None
    self.bot = bot

  async def send_view(self, interaction: discord.Interaction, first=False):
    match = "match" if len(self.books) == 1 else "matches"
    args = {
        "content": f"Showing match **{self.i+1}/{len(self.books)}**. Use the controls to finalize:",
        "embed": embed_from_book(self.books[self.i], interaction.guild),
        "view": self
    }
    if not self.view_message:
      self.view_message = await interaction.followup.send(**args)
      self.bot.add_view(self, message_id=self.view_message.id)
    else:
      await interaction.response.edit_message(**args)

  async def disable_view(self, interaction: discord.Interaction, bye_message: str):
    # original_message = await self.original_itx.original_message()
    for button in self.children:
      button.disabled = True
    await self.send_view(interaction)
    self.stop()
    await interaction.followup.send(content=bye_message, ephemeral=True)
    await self.original_message.delete(delay=DELAY)


  @ui.button(emoji=PREVIOUS_EMOJI, style=discord.ButtonStyle.secondary, custom_id="control_previous")
  async def previous(self, interaction: discord.Interaction, button: ui.Button):
    self.i = (self.i - 1) % len(self.books)
    await self.send_view(interaction)

  @ui.button(emoji=NEXT_EMOJI, style=discord.ButtonStyle.secondary, custom_id="control_next")
  async def next(self, interaction: discord.Interaction, button: ui.Button):
    self.i = (self.i + 1) % len(self.books)
    await self.send_view(interaction)

  @ui.button(label="Edit", emoji=EDIT_EMOJI, style=discord.ButtonStyle.secondary, custom_id="control_edit")
  async def edit(self, interaction: discord.Interaction, button: ui.Button):
    modal = EditBookModal(self, self.books[self.i])
    await interaction.response.send_modal(modal)

    # Wait for the modal to submit.
    result = await modal.await_submit()
    if result:
      itx, self.books[self.i] = result
      await self.send_view(itx)
    else:
      if not itx.response.done():
        await itx.response.send_message("Something went wrong editing your book.", ephemeral=True)

  @ui.button(label="Cancel", emoji=CANCEL_EMOJI, style=discord.ButtonStyle.secondary, custom_id="control_cancel")
  async def cancel(self, interaction: discord.Interaction, button: ui.Button):
    await self.disable_view(
        interaction,
        f"Your book submission has been canceled and will be removed in {DELAY} seconds.")

  @ui.button(label="Submit", emoji=SUBMIT_EMOJI, style=discord.ButtonStyle.secondary, custom_id="control_submit")
  async def submit(self, interaction: discord.Interaction, button: ui.Button):
    finalized_book = FinalizedBook(self.books[self.i])
    await finalized_book.send_message(interaction)

  async def on_error(self, itx: discord.Interaction, error: Exception, item: ui.Item):
    traceback.print_exception(error)
    await itx.response.send_message(f"Error adding book: {str(error)}.", ephemeral=True)


class BookoCog(commands.Cog):
  def __init__(self, bot: commands.Bot, google_books_api, open_library_api, goodreads_api):
    self.bot = bot
    self.google_books_api = google_books_api
    self.open_library_api = open_library_api
    self.goodreads_api = goodreads_api
    self.channel_map = None

  def get_channel(self, channel_id: int):
    channel = self.bot.get_channel(channel_id)
    if not channel:
      print(f"Unable to find channel {channel_id}.")
    return channel

  @commands.Cog.listener()
  async def on_ready(self):
    print("Initializing...")

    self.voting_channel = self.get_channel(CONFIG.voting_id)
    self.recommendations_channel = self.get_channel(CONFIG.recommendations_id)
    self.past_books_channel = self.get_channel(CONFIG.past_books_id)
    self.smut_channel = self.get_channel(CONFIG.smut_id)

    self.channel_map = {
        self.recommendations_channel.id: Shelf.RECOMMENDED,
        self.past_books_channel.id: Shelf.READ,
        self.smut_channel.id: Shelf.SMUT
    }

    if not any((self.voting_channel, self.recommendations_channel, self.past_books_channel, self.smut_channel)):
      print("Shutting down...")
      await self.bot.close()
      return

    guild = self.bot.get_guild(CONFIG.guild_id)
    if not guild:
      print(f"Unable to find guild: {CONFIG.guild_id}.")
      print("Shutting down...")
      await self.bot.close()
      return

    await self.bot.tree.sync(guild=guild)

    with Session() as session:
      for book in session.execute(select(Book)).scalars():
        self.bot.add_view(FinalizedBook(book), message_id=book.message_id)

    print(f"Running in {guild.name}!")

  def get_books(self, author: str, title: str, shelf: Shelf, user_id: int):
    books = self.google_books_api.search_author_title(author, title)
    for book in books:
      book.thumbnail_url = self.google_books_api.thumbnail_from_isbn(book.isbn)
      if not book.thumbnail_url:
        book.thumbnail_url = self.open_library_api.thumbnail_from_isbn(book.isbn)
      book.open_library_url = self.open_library_api.link_from_isbn(book.isbn)
      book.goodreads_url = self.goodreads_api.link_from_isbn(book.isbn)
      book.shelf = shelf
      book.user_id = user_id

    return books

  @app_commands.command(description="Adds a new book.")
  @app_commands.guilds(CONFIG.guild_id)
  @app_commands.describe(suggester="the user who suggested the book (defaults to you)")
  async def add_book(self, itx: discord.Interaction, title: str, author: str, suggester: discord.User = None):
    if itx.channel.id not in self.channel_map:
      raise app_commands.AppCommandError("Invalid channel for this command!")

    # Default to the user
    if not suggester:
      suggester = itx.user

    await itx.response.defer(thinking=True)
    shelf = self.channel_map[itx.channel.id]
    books = self.get_books(author, title, shelf, suggester.id)
    if not books:
      await itx.followup.send(
          f"Unable to find any books matching: *{title}* by {author}.", ephemeral=True, wait=True)
      return

    original_message = await itx.original_message()
    view = BookChoice(self.bot, books, original_message)
    await view.send_view(itx, first=True)

  @add_book.error
  async def on_add_book_error(self, itx: discord.Interaction, error: app_commands.AppCommandError):
    traceback.print_exception(error)
    if not itx.response.is_done():
      await itx.response.send_message(str(error), ephemeral=True)
    else:
      await itx.followup.send(str(error), ephemeral=True)


async def main():
  parser = argparse.ArgumentParser()
  parser.add_argument(
      "discord_token", help="The path to your Discord bot token.")
  parser.add_argument(
      "--google_books_key", default="data/books_api", help="The Google Books API key.")
  parser.add_argument(
      "--database", default="data/test_alchemy.db", help="The Sqlite3 database to use.")
  parser.add_argument(
      "--verbose_api", action="store_true", help="Whether or not to verbosely log API calls.")
  parser.add_argument(
      "--verbose_db", action="store_true", help="Whether or not to verbosely log the database.")
  args = parser.parse_args()

  global Session
  models.initialize(args.database)
  Session = models.Session

  with open(args.discord_token, "r") as token_file:
    discord_token = token_file.read().strip()

  with open(args.google_books_key, "r") as key_file:
    google_books_key = key_file.read().strip()

  intents = discord.Intents.default()
  intents.members = True
  bot = commands.Bot("!", intents=intents)

  google_books_api = GoogleBooksApi(google_books_key, args.verbose_api)
  open_library_api = OpenLibraryApi(args.verbose_api)
  goodreads_api = GoodreadsApi(args.verbose_api)

  async with bot:
    await bot.add_cog(BookoCog(bot, google_books_api, open_library_api, goodreads_api))
    await bot.start(discord_token)


if __name__ == "__main__":
  asyncio.run(main())
