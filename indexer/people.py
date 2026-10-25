"""Manual person and email-alias management for the case index."""

__author__ = "Bálint Szabolcs"

from dataclasses import dataclass
import sqlite3

from .database import IndexDatabase


@dataclass(frozen=True)
class Person:
    """A person and the email addresses assigned to them."""

    id: int
    display_name: str
    addresses: tuple[str, ...]


@dataclass(frozen=True)
class PersonSuggestion:
    """A non-binding suggestion to group addresses with a shared display name."""

    display_name: str
    addresses: tuple[str, ...]
    assigned_person_ids: tuple[int, ...]


class PersonDirectory:
    """Expose person management without changing source messages."""

    def __init__(self, database: IndexDatabase) -> None:
        """Use the case database for aliases and person records."""
        self.database = database

    def create_person(
        self, display_name: str, addresses: tuple[str, ...] = ()
    ) -> Person:
        """Create a person with optional known email addresses."""
        person_id = self.database.create_person(display_name, addresses)
        return next(person for person in self.people() if person.id == person_id)

    def assign_address(
        self, person_id: int, address: str, display_name: str = ""
    ) -> Person:
        """Add an address alias to an existing person."""
        self.database.assign_address(person_id, address, display_name)
        return self._get_person(person_id)

    def remove_address(self, person_id: int, address: str) -> bool:
        """Remove an address alias while leaving indexed source participants intact."""
        return self.database.remove_address(person_id, address)

    def rename(self, person_id: int, display_name: str) -> Person:
        """Set a person's canonical display name."""
        self.database.rename_person(person_id, display_name)
        return self._get_person(person_id)

    def merge(self, target_id: int, source_id: int) -> Person:
        """Merge one person and all of their aliases into another person."""
        self.database.merge_people(target_id, source_id)
        return self._get_person(target_id)

    def people(self) -> list[Person]:
        """List all people, collecting each assigned alias once."""
        rows = self.database.people()
        people: dict[int, tuple[str, list[str]]] = {}
        for row in rows:
            person_id = int(row["id"])
            if person_id not in people:
                people[person_id] = (str(row["display_name"]), [])
            if row["address"] is not None:
                people[person_id][1].append(str(row["address"]))
        return [
            Person(
                id=person_id,
                display_name=display_name,
                addresses=tuple(sorted(addresses)),
            )
            for person_id, (display_name, addresses) in people.items()
        ]

    def suggestions(self) -> list[PersonSuggestion]:
        """Group distinct observed addresses by display name for manual review."""
        grouped: dict[str, tuple[str, set[str], set[int], set[str]]] = {}
        for row in self.database.person_suggestion_rows():
            display_name = str(row["display_name"]).strip()
            key = display_name.casefold()
            if not display_name or not key:
                continue
            if key not in grouped:
                grouped[key] = (display_name, set(), set(), set())
            grouped[key][1].add(str(row["address"]))
            if row["person_id"] is not None:
                grouped[key][2].add(int(row["person_id"]))
            else:
                grouped[key][3].add(str(row["address"]))

        suggestions = [
            PersonSuggestion(
                display_name=display_name,
                addresses=tuple(sorted(addresses)),
                assigned_person_ids=tuple(sorted(person_ids)),
            )
            for display_name, addresses, person_ids, unassigned in grouped.values()
            if len(addresses) > 1 and (len(person_ids) != 1 or unassigned)
        ]
        return sorted(
            suggestions,
            key=lambda suggestion: (suggestion.display_name.casefold(), suggestion.addresses),
        )

    def search(
        self,
        query: str,
        person_ids: tuple[int, ...],
        limit: int = 100,
        *,
        include_cc_bcc: bool = True,
    ) -> list[sqlite3.Row]:
        """Search FTS content for messages involving selected people."""
        if not person_ids:
            raise ValueError("At least one person must be selected")
        return self.database.search(
            query,
            limit,
            person_ids=person_ids,
            include_cc_bcc=include_cc_bcc,
        )

    def _get_person(self, person_id: int) -> Person:
        """Return one person by ID or raise when it does not exist."""
        for person in self.people():
            if person.id == person_id:
                return person
        raise ValueError(f"Person does not exist: {person_id}")
