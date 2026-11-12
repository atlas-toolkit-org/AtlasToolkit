# Name, logo and official presets

The code is free software under the GPL. You may copy it, change it and distribute your own builds, as the licence
allows. The **name "Atlas Toolkit"** and **its logo** are not covered by that licence. They tell people that a build
or a pack comes from the official project, by sci.co.plot.

## Allowed

- Saying that your work uses, works with or is based on Atlas Toolkit, for example "a fork of Atlas Toolkit", or
  "presets for Atlas Toolkit".
- Packaging an **unmodified** official release for a Linux distribution, or any other repository, under its name.
- Using the name in articles, tutorials, videos and reviews.

## Ask first (hello@atlas-toolkit.org)

- Distributing a **modified** build under the name "Atlas Toolkit" or with its logo.
  - A fork needs its own name and icon: change `APP_NAME` and `APP_ID` in `src/atlas_toolkit/version.py`, and the
    logo.
- Using the name or logo for a product, a shop or a domain name.

## Official presets

Official preset packs are signed with the publisher's keys (`src/atlas_toolkit/core/preset_keys.py`), and the app
shows them as **Official**.
- Forks may change those keys or remove the check. They must not present other packs as official Atlas Toolkit
  presets.
- Packs you make yourself are welcome. Sign them with your own key, and users can choose to trust you as a publisher.
