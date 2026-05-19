"""
PetSwipe — a mobile-first swipe-to-vote web app for adoptable pets.

CMPE 285 Software Engineering — final exam project.

Single-file Flask backend:
  * Serves the single-page frontend (templates/index.html).
  * Owns a tiny SQLite database (petswipe.db) created and seeded on first run.
  * Exposes a small JSON API for items, voting, aggregate results, and stats.

Run with:
    pip install flask
    python app.py
Then open http://127.0.0.1:5000
"""

import os
import sqlite3

from flask import Flask, g, jsonify, render_template, request

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "petswipe.db")

# Valid species values (kept in sync with the seed data and the frontend filter).
SPECIES = ("dog", "cat", "rabbit", "bird", "hamster")

app = Flask(__name__)


# --------------------------------------------------------------------------- #
# Database helpers
# --------------------------------------------------------------------------- #

def get_db():
    """Return a per-request SQLite connection (row access by column name)."""
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        # Enforce the FOREIGN KEY constraint on the votes table.
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(_exception):
    """Close the request-scoped database connection, if any."""
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    """Create tables if they don't exist and seed 100 pets on first run."""
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")

    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            breed TEXT NOT NULL,
            species TEXT NOT NULL,
            age TEXT NOT NULL,
            description TEXT NOT NULL,
            image_url TEXT NOT NULL,
            personality TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS votes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id INTEGER NOT NULL,
            session_id TEXT NOT NULL,
            choice TEXT NOT NULL CHECK(choice IN ('yes', 'no')),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (item_id) REFERENCES items(id),
            UNIQUE(item_id, session_id)
        );
        """
    )

    # Seed only when the items table is empty so re-running app.py is safe.
    already_seeded = conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
    if already_seeded == 0:
        rows = []
        for name, breed, species, age, description, personality in SEED_PETS:
            # image_url uses the soon-to-be-assigned row id so each pet has a
            # stable, unique image. AUTOINCREMENT starts at 1 on an empty table.
            placeholder_id = len(rows) + 1
            image_url = f"https://picsum.photos/seed/pet{placeholder_id}/400/500"
            rows.append(
                (name, breed, species, age, description, image_url, personality)
            )

        conn.executemany(
            """
            INSERT INTO items
                (name, breed, species, age, description, image_url, personality)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        conn.commit()
        print(f"Seeded {len(rows)} pets into {DB_PATH}")

    conn.close()


# --------------------------------------------------------------------------- #
# API routes
# --------------------------------------------------------------------------- #

@app.route("/")
def index():
    """Serve the single-page frontend."""
    return render_template("index.html")


@app.route("/api/items")
def api_items():
    """
    Return every pet, annotated with ``voted`` for the given session.

    Query params:
        session_id (optional): when present, each item gets ``voted: true``
        if this session already voted on it (used to resume the deck).
    """
    session_id = request.args.get("session_id", "")
    db = get_db()

    items = db.execute(
        """
        SELECT id, name, breed, species, age, description, image_url, personality
        FROM items
        ORDER BY id
        """
    ).fetchall()

    # One query for the whole set of items this session already voted on.
    voted_ids = set()
    if session_id:
        voted_rows = db.execute(
            "SELECT item_id FROM votes WHERE session_id = ?", (session_id,)
        ).fetchall()
        voted_ids = {row["item_id"] for row in voted_rows}

    payload = []
    for item in items:
        record = dict(item)
        record["voted"] = item["id"] in voted_ids
        payload.append(record)

    return jsonify(payload)


@app.route("/api/vote", methods=["POST"])
def api_vote():
    """
    Record a single vote.

    Expected JSON body:
        { "itemId": int, "choice": "yes"|"no", "sessionId": str }

    Idempotent: the UNIQUE(item_id, session_id) constraint means a repeat
    vote for the same pet in the same session is silently ignored rather
    than creating a duplicate or erroring out.
    """
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Request body must be JSON."}), 400

    item_id = data.get("itemId")
    choice = data.get("choice")
    session_id = data.get("sessionId")

    # --- Input validation ------------------------------------------------- #
    if not isinstance(item_id, int) or isinstance(item_id, bool):
        return jsonify({"error": "itemId must be an integer."}), 400
    if choice not in ("yes", "no"):
        return jsonify({"error": "choice must be 'yes' or 'no'."}), 400
    if not isinstance(session_id, str) or not session_id.strip():
        return jsonify({"error": "sessionId must be a non-empty string."}), 400

    db = get_db()

    # The referenced pet must exist.
    exists = db.execute(
        "SELECT 1 FROM items WHERE id = ?", (item_id,)
    ).fetchone()
    if exists is None:
        return jsonify({"error": f"No pet with id {item_id}."}), 400

    # INSERT OR IGNORE makes the duplicate case a graceful no-op (idempotency).
    cursor = db.execute(
        """
        INSERT OR IGNORE INTO votes (item_id, session_id, choice)
        VALUES (?, ?, ?)
        """,
        (item_id, session_id.strip(), choice),
    )
    db.commit()

    # rowcount == 0 means the unique constraint blocked a duplicate vote.
    duplicate = cursor.rowcount == 0
    return jsonify({"ok": True, "duplicate": duplicate})


@app.route("/api/results")
def api_results():
    """
    Aggregate yes/no counts per pet across ALL sessions.

    Query params:
        sort: one of 'most-loved' (default), 'most-divisive', 'most-voted'.

    Sorting semantics:
        most-loved    -> highest approval %, ties broken by more 'yes' votes
        most-divisive -> closest to a 50/50 split, weighted toward pets that
                         actually have votes (pets with 0 votes sink to bottom)
        most-voted    -> highest total number of votes
    """
    sort = request.args.get("sort", "most-loved")
    if sort not in ("most-loved", "most-divisive", "most-voted"):
        sort = "most-loved"

    db = get_db()

    # LEFT JOIN so pets with zero votes still appear in the results list.
    rows = db.execute(
        """
        SELECT
            i.id, i.name, i.breed, i.species, i.age,
            i.image_url, i.personality,
            COALESCE(SUM(CASE WHEN v.choice = 'yes' THEN 1 ELSE 0 END), 0) AS yes_count,
            COALESCE(SUM(CASE WHEN v.choice = 'no'  THEN 1 ELSE 0 END), 0) AS no_count
        FROM items i
        LEFT JOIN votes v ON v.item_id = i.id
        GROUP BY i.id
        """
    ).fetchall()

    results = []
    for row in rows:
        record = dict(row)
        total = record["yes_count"] + record["no_count"]
        record["total_votes"] = total
        record["yes_pct"] = round((record["yes_count"] / total) * 100, 1) if total else 0.0
        results.append(record)

    if sort == "most-loved":
        results.sort(key=lambda r: (r["yes_pct"], r["yes_count"]), reverse=True)
    elif sort == "most-voted":
        results.sort(key=lambda r: r["total_votes"], reverse=True)
    else:  # most-divisive
        # Pets with no votes can't be divisive — push them to the very end.
        def divisive_key(r):
            if r["total_votes"] == 0:
                return (1, 0, 0)  # rank after every pet that has votes
            closeness = abs(r["yes_pct"] - 50)  # 0 == perfectly split
            return (0, closeness, -r["total_votes"])

        results.sort(key=divisive_key)

    return jsonify({"sort": sort, "results": results})


@app.route("/api/stats")
def api_stats():
    """Return overall totals shown in the results header."""
    db = get_db()
    total_votes = db.execute("SELECT COUNT(*) FROM votes").fetchone()[0]
    total_sessions = db.execute(
        "SELECT COUNT(DISTINCT session_id) FROM votes"
    ).fetchone()[0]
    total_items = db.execute("SELECT COUNT(*) FROM items").fetchone()[0]

    return jsonify(
        {
            "total_votes": total_votes,
            "total_sessions": total_sessions,
            "total_items": total_items,
        }
    )


# --------------------------------------------------------------------------- #
# Seed data — exactly 100 pets (40 dogs, 30 cats, 12 rabbits, 10 birds,
# 8 hamsters). Every name is unique. Tuple order matches the INSERT above:
# (name, breed, species, age, description, personality)
# --------------------------------------------------------------------------- #

SEED_PETS = [
    # ---- 40 dogs --------------------------------------------------------- #
    ("Captain Biscuit", "Golden Retriever", "dog", "3 years", "A sunshine-soaked goofball who is convinced every stranger is a long-lost best friend.", "Playful & Energetic"),
    ("Luna", "Siberian Husky", "dog", "2 years", "Talks back constantly and will absolutely sing the song of her people at 6am.", "Vocal & Spirited"),
    ("Sir Fluffington", "Samoyed", "dog", "4 years", "A walking cloud with a permanent smile and an aristocratic taste for belly rubs.", "Gentle & Regal"),
    ("Mochi", "Shiba Inu", "dog", "1 year", "Stubborn, sassy, and absolutely certain he is the main character of every story.", "Independent & Bold"),
    ("Ziggy", "Border Collie", "dog", "3 years", "Will herd your kids, your guests, and possibly your roomba with great enthusiasm.", "Smart & Driven"),
    ("Waffles", "Pembroke Welsh Corgi", "dog", "2 years", "Short legs, enormous dreams, and a sploot that could end wars.", "Cheerful & Cuddly"),
    ("Bruno", "German Shepherd", "dog", "5 years", "A loyal gentleman who takes his job as your devoted shadow very seriously.", "Loyal & Protective"),
    ("Peanut", "Beagle", "dog", "1 year", "Nose first, brain later — an adorable scent-powered chaos engine.", "Curious & Friendly"),
    ("Daisy", "Labrador Retriever", "dog", "4 years", "Never met a tennis ball, puddle, or snack she did not love instantly.", "Joyful & Loving"),
    ("Tofu", "Standard Poodle", "dog", "3 years", "Elegant on the outside, complete circus clown on the inside.", "Clever & Charming"),
    ("Rocky", "Boxer", "dog", "2 years", "A muscular bundle of zoomies who hugs with his entire body.", "Energetic & Affectionate"),
    ("Maple", "Australian Shepherd", "dog", "3 years", "Brilliant, beautiful, and three steps ahead of you at all times.", "Smart & Active"),
    ("Noodle", "Dachshund", "dog", "5 years", "Long, low, and fiercely brave despite being mostly the size of a loaf.", "Bold & Devoted"),
    ("Nala", "Rottweiler", "dog", "4 years", "A big softie who is firmly convinced she is a 90-pound lapdog.", "Calm & Protective"),
    ("Pepper", "Dalmatian", "dog", "2 years", "Spotted, springy, and powered by what can only be described as pure caffeine.", "Spirited & Playful"),
    ("Gus", "English Bulldog", "dog", "6 years", "A wrinkly philosopher who has elevated the art of napping to fine art.", "Mellow & Affectionate"),
    ("Coco", "Cavalier King Charles Spaniel", "dog", "3 years", "A velvet-eared sweetheart who exists purely to be adored.", "Gentle & Cuddly"),
    ("Bandit", "Jack Russell Terrier", "dog", "2 years", "Tiny dog, enormous personality, zero concept of personal limits.", "Feisty & Fearless"),
    ("Willow", "Bernese Mountain Dog", "dog", "4 years", "A gentle giant who leans on you because hugs are her primary love language.", "Gentle & Loyal"),
    ("Taco", "Chihuahua", "dog", "3 years", "Five pounds of pure attitude wrapped in a tiny trembling burrito.", "Sassy & Devoted"),
    ("Scout", "English Pointer", "dog", "3 years", "Born to explore, freezes dramatically the instant he spots a butterfly.", "Adventurous & Focused"),
    ("Honey", "Cocker Spaniel", "dog", "5 years", "Sweet as her name with ears that double as deluxe mop accessories.", "Gentle & Sweet"),
    ("Diesel", "Doberman Pinscher", "dog", "4 years", "Sleek, sharp, and surprisingly clingy the moment nobody is watching.", "Alert & Loyal"),
    ("Pretzel", "Basset Hound", "dog", "6 years", "Droopy, dignified, and an Olympic-level professional sulker.", "Laid-back & Stubborn"),
    ("Kiwi", "Shetland Sheepdog", "dog", "2 years", "A fluffy little blur of sharp intelligence and very dramatic barking.", "Smart & Vocal"),
    ("Bear", "Newfoundland", "dog", "5 years", "A swimming, drooling, world-class cuddle mountain in dog form.", "Gentle & Patient"),
    ("Olive", "Whippet", "dog", "3 years", "A couch-shaped noodle that transforms into a rocket the second she is outdoors.", "Calm & Swift"),
    ("Marshmallow", "Great Pyrenees", "dog", "4 years", "A majestic snow-colored guardian who patrols the yard like visiting royalty.", "Calm & Protective"),
    ("Rusty", "Irish Setter", "dog", "3 years", "All flowing red hair and zero impulse control — a truly glorious mess.", "Energetic & Friendly"),
    ("Pickle", "Pug", "dog", "4 years", "Snorts, snores, and stares directly into your soul for a bite of your sandwich.", "Charming & Cuddly"),
    ("Aspen", "Alaskan Malamute", "dog", "3 years", "Powerful, fluffy, and convinced winter was personally invented for her.", "Strong & Friendly"),
    ("Toby", "Soft Coated Wheaten Terrier", "dog", "2 years", "A bouncing wheat-colored optimist who greets all of life face-first.", "Happy & Energetic"),
    ("Sage", "Vizsla", "dog", "4 years", "A velcro dog who will follow you into the bathroom without a shred of shame.", "Affectionate & Active"),
    ("Dumpling", "French Bulldog", "dog", "2 years", "Bat-eared, snorty, and a reigning champion of the dramatic flop.", "Playful & Cuddly"),
    ("Ranger", "Weimaraner", "dog", "3 years", "A silver ghost with soulful eyes and a frankly bottomless tank of energy.", "Loyal & Active"),
    ("Clover", "Cardigan Welsh Corgi", "dog", "3 years", "Big-tailed cousin of chaos with a heart absolutely brimming with mischief.", "Cheerful & Smart"),
    ("Moose", "English Mastiff", "dog", "5 years", "Enormous, gentle, and entirely and adorably unaware of his own size.", "Calm & Devoted"),
    ("Pippin", "Papillon", "dog", "4 years", "Butterfly ears, dancer's feet, and a surprisingly bossy little attitude.", "Lively & Clever"),
    ("Hazel", "Brittany Spaniel", "dog", "3 years", "A freckled field artist who lives for long, muddy, joyful adventures.", "Energetic & Sweet"),
    ("Duke", "Bloodhound", "dog", "6 years", "A wrinkled detective whose famous nose has solved many a snack mystery.", "Laid-back & Determined"),

    # ---- 30 cats --------------------------------------------------------- #
    ("Whiskers", "Tabby", "cat", "3 years", "A classic stripey opportunist who personally supervises all human activity.", "Curious & Independent"),
    ("Cleo", "Siamese", "cat", "4 years", "Loud, opinionated, and the self-declared queen of every single room.", "Vocal & Regal"),
    ("Biscotti", "Maine Coon", "cat", "5 years", "A magnificent fluffy giant who genuinely fetches better than most dogs.", "Gentle & Playful"),
    ("Mittens", "Persian", "cat", "6 years", "A grumpy luxurious pillow who tolerates your existence on her good days.", "Calm & Aloof"),
    ("Pumpkin", "Ragdoll", "cat", "3 years", "Goes completely limp with joy the very second you pick him up.", "Docile & Cuddly"),
    ("Shadow", "Bombay", "cat", "2 years", "A sleek living-room panther who utterly melts for under-chin scratches.", "Affectionate & Sleek"),
    ("Pixel", "Bengal", "cat", "2 years", "A miniature leopard running on the energy of a caffeinated toddler.", "Energetic & Wild"),
    ("Marble", "Scottish Fold", "cat", "4 years", "Folded ears, owl face, and a permanent expression of mild polite surprise.", "Sweet & Mellow"),
    ("Gizmo", "Sphynx", "cat", "3 years", "A warm wrinkly heat-seeking missile with absolutely no concept of boundaries.", "Affectionate & Goofy"),
    ("Duchess", "British Shorthair", "cat", "5 years", "A plush teddy-bear aristocrat who prefers to be admired from a respectful distance.", "Calm & Dignified"),
    ("Nibbles", "American Shorthair", "cat", "4 years", "An easygoing classic who is, honestly, just genuinely happy to be here.", "Friendly & Easygoing"),
    ("Smokey", "Russian Blue", "cat", "6 years", "A shy silver gentleman who bonds for life with his one chosen human.", "Gentle & Reserved"),
    ("Tigger", "Orange Tabby", "cat", "2 years", "Two brain cells, infinite confidence, and peak chaotic gremlin energy.", "Goofy & Bold"),
    ("Jasmine", "Abyssinian", "cat", "3 years", "An acrobatic explorer who treats your bookshelf as a personal mountain range.", "Active & Curious"),
    ("Boo", "Tuxedo", "cat", "4 years", "Dressed for a black-tie wedding, behaves like a tiny tap-dancing menace.", "Playful & Charming"),
    ("Cinnamon", "Somali", "cat", "3 years", "A fox-tailed firework of fluff and absolutely relentless curiosity.", "Lively & Affectionate"),
    ("Pearl", "Turkish Angora", "cat", "5 years", "A graceful white dancer who shimmers gloriously and clearly knows it.", "Elegant & Playful"),
    ("Oreo", "Domestic Longhair", "cat", "4 years", "A black-and-white floof who sheds love and fur in roughly equal measure.", "Sweet & Cuddly"),
    ("Loki", "Norwegian Forest Cat", "cat", "3 years", "A majestic woodland viking who climbs every single thing he absolutely should not.", "Adventurous & Gentle"),
    ("Misty", "Chartreux", "cat", "6 years", "A softly smiling blue-gray companion who follows you quietly everywhere.", "Calm & Devoted"),
    ("Binx", "Devon Rex", "cat", "2 years", "Pixie ears, a curly coat, and pure mischievous little gremlin energy.", "Playful & Mischievous"),
    ("Saffron", "Burmese", "cat", "4 years", "A golden-eyed velvet shadow who insists on being a permanent lap accessory.", "Affectionate & Social"),
    ("Onyx", "Domestic Shorthair", "cat", "3 years", "A sleek midnight charmer with surprisingly chatty opinions about everything.", "Friendly & Vocal"),
    ("Coconut", "Birman", "cat", "5 years", "Silky, blue-eyed, and gently convinced she is sacred — and she is.", "Gentle & Sweet"),
    ("Sushi", "Japanese Bobtail", "cat", "3 years", "A bobbed-tail bundle of luck who plays fetch with tremendous personal pride.", "Active & Friendly"),
    ("Bella", "Calico", "cat", "4 years", "A tri-colored diva with rapid mood swings and a perfectly matching attitude.", "Sassy & Independent"),
    ("Pawsley", "Manx", "cat", "5 years", "A tailless little hopper with the loyal devoted soul of a puppy.", "Playful & Loyal"),
    ("Velvet", "Tonkinese", "cat", "3 years", "A people-obsessed charmer who efficiently turns total strangers into staff.", "Social & Affectionate"),
    ("Frost", "Snowshoe", "cat", "4 years", "White-pawed, blue-eyed, and dramatically devoted to exactly one human.", "Sweet & Devoted"),
    ("Toast", "Domestic Shorthair", "cat", "2 years", "Warm, round, and perpetually and sincerely optimistic about breakfast.", "Easygoing & Friendly"),

    # ---- 12 rabbits ------------------------------------------------------ #
    ("Thumper", "Holland Lop", "rabbit", "2 years", "A floppy-eared cuddle puff who binkies with joy whenever life is good.", "Gentle & Playful"),
    ("Sorrel", "Netherland Dwarf", "rabbit", "1 year", "Tiny, perfectly round, and full of surprisingly enormous opinions.", "Curious & Feisty"),
    ("Cocoa", "Mini Rex", "rabbit", "3 years", "Velvet-soft and famously, helplessly addicted to gentle forehead pets.", "Calm & Affectionate"),
    ("Snowball", "Lionhead", "rabbit", "2 years", "A fluffy-maned drama bun who poses dramatically like tiny royalty.", "Gentle & Charming"),
    ("Hazelnut", "Flemish Giant", "rabbit", "4 years", "An enormous gentle lap-rabbit who is convinced he is a small dog.", "Mellow & Friendly"),
    ("Pip", "Mini Lop", "rabbit", "1 year", "A bouncing bundle of zoomies and pure lettuce-fueled rabbit joy.", "Playful & Sweet"),
    ("Crumpet", "Dutch", "rabbit", "3 years", "Tuxedo-marked and very politely demanding about his snack schedule.", "Friendly & Curious"),
    ("Marigold", "English Angora", "rabbit", "2 years", "A walking pom-pom who requires daily brushing and a standing ovation.", "Calm & Gentle"),
    ("Domino", "Harlequin", "rabbit", "3 years", "A patchwork jester bun with a goofy, endlessly curious streak.", "Playful & Curious"),
    ("Fern", "English Lop", "rabbit", "2 years", "Ears longer than her whole body and a heart roughly twice as big.", "Gentle & Docile"),
    ("Squash", "Californian", "rabbit", "4 years", "A chill orange-eyed observer who supervises calmly from a cozy corner.", "Calm & Mellow"),
    ("Nutmeg", "Polish", "rabbit", "1 year", "Pocket-sized perpetual motion fueled entirely by relentless curiosity.", "Lively & Curious"),

    # ---- 10 birds -------------------------------------------------------- #
    ("Tangerine", "Parakeet", "bird", "1 year", "A chatty pocket parrot who proudly whistles showtunes wildly off-key.", "Social & Lively"),
    ("Mango", "Cockatiel", "bird", "2 years", "A crest-bobbing charmer who enthusiastically head-bangs to any beat.", "Playful & Affectionate"),
    ("Sky", "Budgerigar", "bird", "1 year", "A tiny blue acrobat who narrates the entire household out loud all day.", "Chatty & Energetic"),
    ("Rio", "Sun Conure", "bird", "3 years", "A feathered sunset with a scream that could wake the dead, adorably.", "Bold & Affectionate"),
    ("Paprika", "Lovebird", "bird", "2 years", "A pint-sized romantic who bonds intensely and snuggles even harder.", "Loving & Feisty"),
    ("Echo", "African Grey", "bird", "5 years", "A brilliant mimic who will absolutely repeat exactly what you said wrong.", "Smart & Sensitive"),
    ("Sunny", "Canary", "bird", "2 years", "A golden little soloist who cheerfully sings the morning into existence.", "Cheerful & Gentle"),
    ("Opal", "Cockatoo", "bird", "4 years", "A cuddly crested diva who dances hard for attention and reliably gets it.", "Affectionate & Dramatic"),
    ("Pesto", "Green Cheek Conure", "bird", "2 years", "A mischievous acrobat who naps in pockets and confidently steals pens.", "Playful & Curious"),
    ("Indigo", "Parrotlet", "bird", "1 year", "The world's tiniest parrot carrying the world's single largest ego.", "Bold & Spirited"),

    # ---- 8 hamsters ------------------------------------------------------ #
    ("Pebble", "Syrian Hamster", "hamster", "1 year", "A dedicated cheek-stuffing hoarder who runs full marathons at 3am.", "Solitary & Active"),
    ("Snickerdoodle", "Winter White Dwarf", "hamster", "1 year", "A teeny round fluffball powered almost entirely by sunflower seeds.", "Gentle & Shy"),
    ("Gingersnap", "Roborovski", "hamster", "1 year", "The fastest tiny blur you will ever completely fail to photograph.", "Energetic & Skittish"),
    ("Butternut", "Campbell's Dwarf", "hamster", "1 year", "A pocket-sized escape artist who appears to hold an engineering degree.", "Curious & Bold"),
    ("Cookie", "Syrian Hamster", "hamster", "1 year", "A golden gentleman who graciously accepts pets in exchange for seeds.", "Friendly & Mellow"),
    ("Acorn", "Chinese Hamster", "hamster", "1 year", "A slender little climber with a surprisingly long and confident attitude.", "Curious & Gentle"),
    ("Pudding", "Winter White Dwarf", "hamster", "1 year", "A round little dumpling who hoards food like the apocalypse is imminent.", "Sweet & Shy"),
    ("Turbo", "Roborovski", "hamster", "1 year", "A sand-surfing speed demon with zero chill and maximum concentrated cute.", "Energetic & Playful"),
]

# Sanity check — fail loudly at import time if the seed list ever drifts.
assert len(SEED_PETS) == 100, f"Expected 100 pets, found {len(SEED_PETS)}"
assert all(p[2] in SPECIES for p in SEED_PETS), "Unknown species in seed data"
assert len({p[0] for p in SEED_PETS}) == 100, "Pet names must be unique"


# --------------------------------------------------------------------------- #
# Entrypoint
# --------------------------------------------------------------------------- #

if __name__ == "__main__":
    init_db()
    # debug=True gives helpful reload + error pages during the exam demo.
    app.run(host="127.0.0.1", port=5000, debug=True)
