# Testing

Two layers: automated tests you run before every push, and a short manual
check on staging before merging to `main`.

## 1. Automated tests (2 seconds, no network)

```bash
pip install -r requirements-dev.txt
pytest -q
```

`tests/test_flows.py` covers:

| Area | What it checks |
|------|----------------|
| Paid signup | Signup without a code returns the Stripe link with the event id; uploads are blocked until paid; a correctly signed `checkout.session.completed` webhook activates the gallery; a badly signed one is rejected |
| Free access code | The right code activates the gallery immediately, no Stripe |
| Wrong access code | Rejected with a clear message, nothing saved |
| Guest upload + gallery | Upload link lands in the event's own folder under `R2_KEY_ROOT`; wrong file types and oversize files are refused; the gallery groups photos by guest and needs the couple's login; delete refuses keys from other events |
| Signup rules | Back from Stripe then using a code reuses the same signup; a paid email can't sign up twice; email login ignores case; weak passwords and bad emails are refused; the email domain check fails open when DNS is down |
| Pages | All pages and the favicon load; no em dashes in any page |

R2 and DNS are faked in the tests, so they never touch real storage.

## 2. Manual check on staging (5 minutes)

Staging: https://wedding-photos-staging.onrender.com (deploys from the working
branch, uses Stripe **test** mode and the `staging/` folder in R2).

1. **Paid:** create a gallery and pay with `4242 4242 4242 4242`, any future
   date, any CVC. You land on the QR page and the gallery opens.
2. **Free pass:** create a gallery, click "Have an access code?", enter the
   staging code (`FREE_ACCESS_CODE` on the staging service). No Stripe page.
3. **Wrong code:** any other code shows "That access code isn't valid."
4. **Upload:** scan the QR with a phone, upload a few photos and a video,
   then check they appear in the gallery, open full screen and download.
5. **Back button:** start a paid signup, press Back on the Stripe page, enter
   the access code with the same email. It should go straight through.
6. **Phone pass:** look at the landing page, signup and gallery on a phone.

Only merge to `main` (production) once all six pass.
