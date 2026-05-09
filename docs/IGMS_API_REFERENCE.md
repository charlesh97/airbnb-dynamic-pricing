# iGMS API Reference

> Auto-generated from `igms_wrapper/client.py` — official docs: <https://www.igms.com/docs/airgms-api/index.html>

**Auth:** `access_token` as URL query string parameter on every request (not Bearer header).

## OAuth Scopes

| Scope | Endpoints |
|---|---|
| `tasks` | `GET /api/v1/tasks`, `GET /api/v1/team-members` |
| `messaging` | `POST /api/v1/message-booking-guest`, `GET /api/v1/message-status`, `GET /api/v1/get-threads`, `GET /api/v1/guests`, `GET /api/v1/hosts` |
| `listings` | `GET /api/v1/listings`, `GET /api/v1/property/{uid}`, `POST /api/v1/set-listing-status`, `GET /api/v1/get-request-status`, `GET /api/v1/bookings`, `GET /api/v1/company` |
| `calendar-control` | `GET /api/v1/get-calendar-data`, `POST /api/v1/set-calendar-data`, `POST /api/v1/set-calendar-batch` |
| `direct-bookings` | `POST /api/v1/book-property`, `POST /api/v2/book-property`, `POST /api/v2/accept-reservation`, `POST /api/v2/decline-reservation`, `POST /api/v2/cancel-booking`, `GET /api/v2/direct-booking-content/{listingUid}` |
| `pricing-management` | `POST /api/v1/calendar`, `POST /api/v1/calendar-batch`, `POST /api/v2/set-property-calendar-control`, `GET /api/v1/hosts` |
| `availability-control` | `POST /api/v2/set-property-calendar-availability` |
| `smart-locks` | `POST /api/v2/set-property-door-code`, `POST /api/v2/set-booking-door-code` |
| `insurance-settings` | `POST /api/v2/set-booking-insurance-policy` |
| `reports` | `POST /api/v1/reservation-report-csv`, `POST /api/v1/tasks-report-csv` |

---

### `accept_reservation()`
**Scope:** `direct-bookings`
**Endpoint:** `POST /api/v2/accept-reservation`

Accept an incoming reservation request.

**Returns:** APIResponse with ``{"status": 0}`` on success.

---
### `book_property()`
**Scope:** `direct-bookings`
**Endpoint:** `POST /api/v1/book-property`

Create or update a direct-booking reservation (v1).

**Returns:** APIResponse with ``{"data": {"reservation_code": ..., "platform_type": ...}}``.

---
### `cancel_booking()`
**Scope:** `direct-bookings`
**Endpoint:** `POST /api/v2/cancel-booking`

Cancel an existing booking.

**Returns:** APIResponse with ``{"status": 0}`` on success.

---
### `decline_reservation()`
**Scope:** `direct-bookings`
**Endpoint:** `POST /api/v2/decline-reservation`

Decline an incoming reservation request.

**Returns:** APIResponse with ``{"status": 0}`` on success.

---
### `download_reservation_report()`
**Scope:** `reports`
**Endpoint:** `POST /api/v1/reservation-report-csv`

Download a CSV reservation report.

**Returns:** APIResponse whose ``payload`` is the raw CSV text. Save to a file with the ``Content-Disposition`` header as filename.

---
### `download_tasks_report()`
**Scope:** `reports`
**Endpoint:** `POST /api/v1/tasks-report-csv`

Download a CSV tasks report.

**Returns:** APIResponse whose ``payload`` is the raw CSV text.

---
### `find_listing_by_name()`

Find a listing by fuzzy name match.

---
### `find_property_by_name()`

Find a property by fuzzy name match.

---
### `get_all_bookings()`

Collect all bookings across all pages.

---
### `get_all_listings()`

Collect all listings across all pages.

---
### `get_all_properties()`

Collect all properties across all pages.

---
### `get_all_threads()`

Collect all threads across all pages.

---
### `get_bookings()`
**Scope:** `listings`
**Endpoint:** `GET /api/v1/bookings`

List bookings (paginated).

---
### `get_calendar()`
**Scope:** `calendar-control`
**Endpoint:** `GET /api/v1/get-calendar-data`

Fetch calendar entries for a property.

**Args:** property_uid: Parent property UID. from_date: Start date ``YYYY-MM-DD``. to_date: End date ``YYYY-MM-DD``.

---
### `get_company()`
**Scope:** `listings (implied)`
**Endpoint:** `GET /api/v1/company`

Get current company info (no parameters).

**Returns:** ``{"data": {"company_uid": ..., "company_name": ..., "contact_name": ..., "contact_email": ..., "contact_phone": ...}}``

---
### `get_direct_booking_content()`
**Scope:** `direct-bookings`
**Endpoint:** `GET /api/v2/direct-booking-content/{listingUid}`

Get detailed direct-booking listing information.

**Returns:** APIResponse with full listing data including pricing, amenities, photos, and policy details.

---
### `get_guests()`
**Scope:** `messaging`
**Endpoint:** `GET /api/v1/guests`

List all guests.

**Args:** guest_uids: Comma-separated list of guest UIDs to filter by. platform_type: Comma-separated platform types (e.g. ``"airbnb,vrbo"``).

---
### `get_hosts()`
**Scope:** `messaging, listings, or pricing-management`
**Endpoint:** `GET /api/v1/hosts`

List all hosts.

**Args:** host_uids: Comma-separated list of host UIDs to filter by. platform_type: Comma-separated platform types (e.g. ``"airbnb,vrbo"``).

---
### `get_listings()`
**Scope:** `listings`
**Endpoint:** `GET /api/v1/listings`

List all listings (paginated).

---
### `get_message_status()`
**Scope:** `messaging`
**Endpoint:** `GET /api/v1/message-status`

Check delivery status of a sent message.

**Returns:** APIResponse with ``{"data": {"message_uid": ..., "message_status": ..., "message_error": "..."}}``. ``message_error`` is only present when ``message_status`` is ``"error"``.

---
### `get_properties()`
**Scope:** `listings`
**Endpoint:** `GET /api/v1/property`

List all properties (paginated).

---
### `get_property()`
**Scope:** `listings`
**Endpoint:** `GET /api/v1/property/{propertyUid}`

Get a single property by UID.

---
### `get_request_status()`
**Scope:** `listings or calendar-control`
**Endpoint:** `GET /api/v1/get-request-status`

Check the processing status of a prior async request.

**Returns:** APIResponse with ``{"data": {"request_uid": ..., "request_status": ..., "request_error": "..."}}``. The ``request_error`` field is only present when ``request_status`` is ``"error"``.

---
### `get_tasks()`
**Scope:** `tasks`
**Endpoint:** `GET /api/v1/tasks`

List active tasks.

---
### `get_team_members()`
**Scope:** `tasks`
**Endpoint:** `GET /api/v1/team-members`

List company team members.

**Args:** member_roles: Comma-separated role names. member_status: One of the available member statuses.

---
### `get_threads()`
**Scope:** `messaging`
**Endpoint:** `GET /api/v1/get-threads`

List message threads (paginated).

---
### `message_booking_guest()`
**Scope:** `messaging`
**Endpoint:** `POST /api/v1/message-booking-guest`

Send a message to the primary guest of a booking.

**Args:** message: Message text content. thread_id: Existing thread UID. booking_uid: Booking UID. channel: 'email' for direct bookings / booking.com; 'platform' for Airbnb/VRBO/etc.

**Returns:** APIResponse with ``{"data": {"message_uid": "..."}}``. Poll with ``get_message_status(message_uid)``.

---
### `propose_calendar_batch()`
**Scope:** `pricing-management`
**Endpoint:** `POST /api/v1/calendar-batch`

Propose per-day prices for separate dates (pricing-management scope).

**Returns:** APIResponse with ``{"status": 0}`` on success.

---
### `propose_calendar_data()`
**Scope:** `pricing-management`
**Endpoint:** `POST /api/v1/calendar  (official method: propose-calendar-data)`

Propose a price for a date range (requires pricing-management scope).

**Args:** property_uid: Property UID. start_date: Range start ``YYYY-MM-DD``. end_date: Range end ``YYYY-MM-DD``. price: Nightly price for the period. currency: 3-letter currency code (default USD). min_stay: Optional minimum stay length. is_user_action: Whether the request was triggered by a user action.

**Returns:** APIResponse with ``{"status": 0}`` on success.

---
### `set_booking_door_code()`
**Scope:** `smart-locks`
**Endpoint:** `POST /api/v2/set-booking-door-code`

Set the smart-lock door code for a specific booking.

**Returns:** APIResponse with ``{"status": 0}`` on success.

---
### `set_booking_insurance_policy()`
**Scope:** `insurance-settings`
**Endpoint:** `POST /api/v2/set-booking-insurance-policy`

Attach insurance policy details to a booking.

**Returns:** APIResponse with ``{"status": 0}`` on success.

---
### `set_calendar_batch()`
**Scope:** `calendar-control`
**Endpoint:** `POST /api/v1/set-calendar-batch`

Set per-day calendar data (calendar-control scope).

**Returns:** APIResponse with ``{"data": {"request_uids": [...], "warnings": {}}}``.

---
### `set_calendar_data()`
**Scope:** `calendar-control`
**Endpoint:** `POST /api/v1/set-calendar-data`

Set calendar data for a date range (calendar-control scope).

**Args:** property_uid: Property UID. start_date: Range start ``YYYY-MM-DD``. end_date: Range end ``YYYY-MM-DD``. price: Nightly price (required if currency is provided). currency: 3-letter currency code (required if price is provided). is_available: 1 to make available, 0 to block. notes: Optional text note for the period. min_stay: Optional minimum stay for the period.

**Returns:** APIResponse with ``{"data": {"request_uid": "..."}}`` on success. Use ``get_request_status(request_uid)`` to poll.

---
### `set_listing_status()`
**Scope:** `listings`
**Endpoint:** `POST /api/v1/set-listing-status`

Enable or disable a listing (Airbnb only).

**Args:** property_uid: UID of the property whose listing to update. status: One of the available listing statuses (e.g. ``"active"``, ``"inactive"``).

**Returns:** APIResponse with ``{"data": {"request_uid": "..."}}`` on success. Use ``get_request_status(request_uid)`` to check processing state.

---
### `set_property_availability()`
**Scope:** `availability-control`
**Endpoint:** `POST /api/v2/set-property-calendar-availability`

Block or unblock dates for a property.

**Args:** property_uid: Property UID. start_date: Range start ``YYYY-MM-DD``. end_date: Range end ``YYYY-MM-DD``. is_available: 1 to make available, 0 to block.

**Returns:** APIResponse with ``{"success": bool, "availability": [...], "errors": [...]}``.

---
### `set_property_calendar_control()`
**Scope:** `pricing-management`
**Endpoint:** `POST /api/v2/set-property-calendar-control`

Enable or disable calendar control for a property.

---
### `set_property_door_code()`
**Scope:** `smart-locks`
**Endpoint:** `POST /api/v2/set-property-door-code`

Set the smart-lock door code for a property.

**Returns:** APIResponse with ``{"status": 0}`` on success.

---
### `v2_book_property()`
**Scope:** `direct-bookings`
**Endpoint:** `POST /api/v2/book-property`

Create or update a direct-booking reservation (v2).

**Returns:** APIResponse with ``{"data": {"reservation_code": ..., "platform_type": ...}}``.

---