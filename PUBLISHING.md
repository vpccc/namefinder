# Publish on GitHub

Use a freshly extracted source release, before running the app. Upload the
contents of the Namefinder folder, not your used installation or this ZIP alone.
The repository root should contain app.py, README.md, LICENSE, .gitignore,
ui/, tests/ and dictionary/.

1. In your GitHub account Settings > Emails, enable **Keep my email addresses
   private**. Web commits then use GitHub's no-reply address. If you later use
   Git locally, configure the no-reply address shown there as your Git commit
   email. Your GitHub username and public profile remain visible.
2. Open https://github.com/new. Name the repository `namefinder`, choose Public,
   and leave the automatic README, .gitignore and license options off: they are
   included here. Click Create repository.
3. Use the empty repository's **uploading an existing file** link, or **Add file >
   Upload files**. Drag the contents of the extracted Namefinder folder onto it,
   including the three subfolders and .gitignore. Check that folder paths are
   preserved and that app.py is at the root. Browser uploads do NOT apply
   .gitignore, so never upload your used app directory.
4. Review the file list. There should be 26 files from the fresh source release,
   with no data/, .env, .venv/, __pycache__/, logs, exports or credentials.
   Commit the upload with a message such as `Initial open-source release`.
5. In repository Settings, use the security options to enable/check secret
   scanning, push protection, CodeQL code scanning and private vulnerability
   reporting where offered. Do not bypass a secret alert without reviewing it.
   GitHub's scanners do not detect every secret, especially custom session tickets.
6. Optional: open **Releases > Draft a new release**, use tag `v7.1`, attach the
   fresh source ZIP, describe the release and publish it. People can also use
   **Code > Download ZIP** from the source repository.

No deployment, GitHub Pages, API keys or repository secrets are needed. Users
run Namefinder locally and supply their own optional webhook/session credentials.
Use two-factor authentication or a passkey for your GitHub account. Public users
can suggest changes, but do not give strangers repository write access or merge
changes without reviewing them.

## License

The application's code is under the included MIT License, attributed to
Namefinder contributors without requiring a personal name or email. It permits
reuse, modification and redistribution, including commercial use, with the
license notice retained. The dictionary retains its original notices in
dictionary/. Platform names and marks belong to their respective owners;
this project is unofficial and is not endorsed by those platforms.

## Future releases

Run `python -m unittest discover -s tests -q`, then `python build_release.py`.
The source ZIP is created in dist/. Only explicitly listed public files are
included, even when the app folder contains private data. Review source changes
for secrets as well: a file allowlist cannot detect credentials added to code.

## Official guides

- https://docs.github.com/en/repositories/creating-and-managing-repositories/creating-a-new-repository
- https://docs.github.com/en/repositories/working-with-files/managing-files/adding-a-file-to-a-repository
- https://docs.github.com/en/account-and-profile/how-tos/email-preferences/setting-your-commit-email-address
- https://docs.github.com/en/code-security/getting-started/quickstart-for-securing-your-repository
- https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/licensing-a-repository
