"""
Gets files from Canvas! of Instructure
Original script by Ben Javicente, modified (essentially rewrote from scratch) by Aaron Becker
"""

import argparse
import dataclasses
import os
import re
import traceback

import colorama
import requests

import requests.exceptions

colorama.init(autoreset=True)


def print_c(string, type_, padding, **kwarg):
    """Prints with color"""
    if type_ == "error":
        padded = " " * (padding * 2) + "! " + string
        print(colorama.Fore.RED + padded, **kwarg)
    elif type_ == "new":
        padded = " " * (padding * 2) + "+ " + string
        print(colorama.Fore.GREEN + padded, **kwarg)
    elif type_ == "group":
        padded = " " * (padding * 2) + string
        print(colorama.Fore.BLACK + colorama.Back.WHITE + padded, **kwarg)
    elif type_ == "existing":
        padded = " " * (padding * 2) + "* " + string
        print(colorama.Fore.YELLOW + padded, **kwarg)
    elif type_ == "item":
        print(" " * (padding * 2) + string, **kwarg)


def get_external_download_url(url: str) -> str:
    """
    This should return an URL where the file can be downloaded.
    Supported sites:
    - docs.google.com
    """

    # Try Google Drive
    exp = re.compile(r"https:\/\/drive\.google\.com\/file\/d\/(?P<id>[^\/]*?)\/")
    result = exp.search(url)
    if result:
        document_id = result.group("id")
        return f"https://docs.google.com/uc?export=download&id={document_id}"

    return ""

def sanitize_filename(filename, replacement="_"):
    """Sanitizes a filename for Windows while preserving slashes (`/` or `\`).
    
    - Removes invalid characters (`<>:"|?*`)
    - Keeps forward (`/`) and backslashes (`\`) so folders remain intact
    - Trims trailing dots and spaces (not allowed in Windows)
    """

    # Windows invalid filename characters **EXCEPT** slashes
    invalid_chars = r'[<>:"|?*]'

    # Replace invalid characters with the given replacement (default: `_`)
    sanitized = re.sub(invalid_chars, replacement, filename)

    # Trim trailing dots and spaces (not allowed in Windows)
    sanitized = sanitized.rstrip(". ")

    return sanitized

def sanitize_path(path, replacement="_"):
    """Sanitizes a full directory or file path for Windows while preserving slashes.
    
    - Removes invalid characters (`<>:"|?*`)
    - Replaces `&` with a safer alternative (`replacement`)
    - Trims trailing dots and spaces (not allowed in Windows)
    """
    # Windows invalid filename characters (excluding slashes)
    invalid_chars = r'[<>:"|?*]'

    # Replace invalid characters in each part of the path
    sanitized_parts = [
        re.sub(invalid_chars, replacement, part).rstrip(". ") for part in path.split(os.sep)
    ]

    # Reconstruct the sanitized path
    return os.sep.join(sanitized_parts)



def get_file_name_by_header(header) -> str:
    """Tries to get the file name from the header"""
    if not header:
        return ""
    exp = re.compile(r"filename=\"(?P<file_name>[^\"]*)\"")
    exp_utf8 = re.compile(r"filename\*=UTF-8''(?P<file_name>[^\"]*)")
    result = exp.search(header)
    result_utf8 = exp_utf8.search(header)
    if result_utf8:
        return requests.utils.unquote(result_utf8.group("file_name"))
    if result:
        return requests.utils.unquote(result.group("file_name"))
    return ""


@dataclasses.dataclass
class CanvasApi:
    """Canvas REST API wrapper"""

    # Check https://canvas.instructure.com/doc/api/

    domain: str
    token: str

    def __url(self, query):
        return "/".join(("https:/", self.domain, "api/v1", query))

    def __get(self, query: str, **kwarg):
        """Performs a GET request to the Canvas API with 250 items per page."""
        # Extract existing parameters if provided
        params = kwarg.pop("params", {})
        
        # Ensure 'per_page' is set to 500
        params["per_page"] = 500  

        response = requests.get(
            url=self.__url(query),
            headers={"Authorization": f"Bearer {self.token}"},
            params=params,  # Correctly merged parameters
            **kwarg,
            timeout=30
        )
        return response.json()

    def get_courses(self, only_favorites: bool = False) -> list:
        """Returns enrolled courses, including active/past/inactive/etc."""

        def ensure_list(value):
            return value if isinstance(value, list) else []

        states = ["active", "completed", "inactive", "invited", "deleted"]
        courses = {}

        for state in states:
            result = self.__get(
                "courses",
                params={
                    "enrollment_state": state,
                    "per_page": 100
                }
            )
            
            if isinstance(result, list):
                for c in result:
                    if isinstance(c, dict) and "id" in c:
                        courses[c["id"]] = c
                print_c(f'Retreived {len(result)} {state} courses', "new", 0)

        if only_favorites:
            favorite_courses = ensure_list(
                self.__get("users/self/favorites/courses", params={"per_page": 100})
            )
            favorite_ids = {
                c["id"] for c in favorite_courses
                if isinstance(c, dict) and "id" in c
            }

            return [c for c in courses.values() if c.get("id") in favorite_ids]

        return list(courses.values())


    def get_folders(self, course_id: int) -> list:
        """Gets the folders of a course (max 250 per page)."""
        return self.__get(f"courses/{course_id}/folders")


    def get_modules(self, course_id: int) -> list:
        """Gets the modules of a course (max 250 per page)."""
        return self.__get(f"courses/{course_id}/modules")


    def get_files_from_folder(self, folder_id: int, recent=True) -> list:
        """Gets the files of a folder (max 250 per page)."""
        params = {"per_page": 250}
        if recent:
            params.update({"sort": "updated_at", "order": "desc"})
        return self.__get(f"folders/{folder_id}/files", params=params)


    def get_modules_items(self, course_id: int, module_id: int) -> list:
        """Gets the module items of a course (max 250 per page)."""
        return self.__get(f"courses/{course_id}/modules/{module_id}/items")


    def get_file_from_id(self, course_id: int, file_id: int) -> dict:
        """Gets a file of a specific course using its ID."""
        return self.__get(f"courses/{course_id}/files/{file_id}")


    def get_folder_from_id(self, course_id: int, folder_id: int) -> dict:
        """Gets a folder from a specific course using its ID."""
        return self.__get(f"courses/{course_id}/folders/{folder_id}")


    def _download_from_pages(self, course_id, course_name):
        """Loops through all available pages and downloads their content if pages are enabled."""
        
        pages_api_url = f"https://{self.domain}/api/v1/courses/{course_id}/pages"
        response = requests.get(
            pages_api_url, 
            headers={"Authorization": f"Bearer {self.token}"},
            params={"per_page": 250},  # Ensuring 250 pages per request
            timeout=30
        )

        if response.status_code != 200:
            print_c(f"Warning: Pages are not enabled for course {course_name}. Skipping.", "error", 1)
            return False

        pages_list = response.json()
        if not pages_list:
            print_c(f"Warning: No pages found for course {course_name}.", "error", 1)
            return False

        print_c(f"Found {len(pages_list)} pages in {course_name}. Downloading all page-linked files...", "new", 1)
        
        for page in pages_list:
            if "url" not in page:
                print_c(f"Warning: Skipping a page in {course_name} due to missing 'url' key.", "error", 2)
                continue
            
            self._download_canvas_page(course_id, f"https://{self.domain}/courses/{course_id}/pages/{page['url']}", course_name)

        return True




@dataclasses.dataclass
class CanvasDownloader(CanvasApi):
    """Canvas file downloader"""

    out_dir: str

    def download_files(self, all_courses=False, use="favorite"):
        """Downloads files from Canvas, including modules, folders, and pages."""
        print_c(f"Getting {'all courses' if not use == 'favorite' else 'only favorited courses (remove -favorite flag to get all)'}...", "existing", 0)
        courses = self.get_courses(all_courses)

        print("Retreived course list:")
        for course in courses:
            try:
                cc = course.get("course_code")
                if cc:
                    print_c(cc, "group", 0)
            except Exception as e:
                print_c("Error getting courses: " + e, type_="error", padding=0)

        if not input("Do you want to continue and download all content? (yY/nN)").lower() == "y":
            print("Exiting. Have a good day :)")
            exit()
        print

        if "errors" in courses:
            print_c("course error: " + courses["errors"][0]["message"], "error", 0)
            return False

        for course in courses:
            if not course.get("course_code"):
                print(f"Course missing valid code, skipping for ID: {course.get('id')}")
                continue

            print_c(course["course_code"], type_="group", padding=0)
            course_code, course_id = course["id"], course["course_code"]

            # Reset cache for each course
            self.download_cache = {}

            methods = [self._download_from_modules, self._download_from_folders, self._download_from_pages]

            if use == "all":
                for method in methods:
                    method(course_code, course_id)
                continue

            if use == "pages":
                method = methods[2]
            elif use == "folders":
                method = methods[1]
            else:
                method = methods[0]

            # Use selected method to download course content
            method(course_code, course_id)

        print("Finished downloading all available courses. Have a great day >:)")
        return True


    def _download_from_folders(self, course_id, course_name) -> bool:
        folders_list = self.get_folders(course_id)
        for folder in folders_list:

            if not folder["files_count"]:
                continue

            files_list = self.get_files_from_folder(folder["id"])

            if "errors" in files_list:
                return False

            folder_path = [course_name] + folder["full_name"].split("/")[1:]
            print_c("[F] " + folder["full_name"], "item", 1)

            for file_obj in files_list:
                if not isinstance(file_obj, dict) or "url" not in file_obj:
                    print_c("Error in _download_from_folders: invalid item, skipping", "error", 2)
                    continue  # Skip invalid objects

                self._download_file(
                    file_obj["url"], folder_path, file_obj["display_name"]
                )

        return True

    def _download_from_modules(self, course_id, course_name) -> bool:
        modules_list = self.get_modules(course_id)

        if not modules_list or "errors" in modules_list:
            print_c(f"Warning: Modules are not available for course {course_name}. Skipping.", "error", 1)
            return False

        for module in modules_list:
            if not module.get("items_count"):
                continue

            module_items = self.get_modules_items(course_id, module["id"])
            if "errors" in module_items:
                print_c(f"Warning: Unable to retrieve module items for {module['name']} in course {course_name}.", "error", 2)
                continue

            module_path = [course_name, module["name"].strip().replace("/", "&").replace(":", "-")]
            print_c("[M] " + module["name"], "item", 1)

            for item in module_items:
                if not isinstance(item, dict) or "type" not in item:
                    print_c("Error in _download_from_modules: invalid item, skipping", "error", 2)
                    continue  # Skip invalid objects

                if item["type"] == "File":
                    file_obj = self.get_file_from_id(course_id, item["content_id"])
                    folder_obj = self.get_folder_from_id(course_id, file_obj["folder_id"])
                    current_folder_path = ([course_name] + folder_obj["full_name"].split("/")[1:]) if "full_name" in folder_obj else module_path
                    if not isinstance(file_obj, dict) or "url" not in file_obj:
                        print_c("Error in module item file downloader: invalid item, skipping", "error", 2)
                        continue  # Skip invalid objects
                    self._download_file(file_obj["url"], current_folder_path, file_obj["display_name"])

                elif item["type"] == "ExternalUrl":
                    if self._is_canvas_url(item["external_url"]):
                        self._download_canvas_page(course_id, item["external_url"], course_name)

                elif item["type"] == "Page":
                    self._download_canvas_page(course_id, f"https://{self.domain}/courses/{course_id}/pages/{item['page_url']}", course_name)

        return True


    def _download_canvas_page(self, course_id, page_url, course_name):
        """Fetches a Canvas page, saves it as an HTML file, and downloads embedded iframes for offline viewing."""

        if isinstance(course_name, list):
            course_name = course_name[0]  # Ensure course_name is a string

        if not page_url or not isinstance(page_url, str) or not page_url.startswith(("http://", "https://")):
            print_c("Error in _download_canvas_page: Invalid URL", "error", 2)
            return

        # Define the "Files" folder for the course
        files_folder = sanitize_path(os.path.join(self.out_dir, course_name, "Files"))
        os.makedirs(files_folder, exist_ok=True)

        # Normalize page URL and check cache
        normalized_page_url = self._normalize_url(page_url)
        # if page_url in self.download_cache:
        #     print_c(f"Skipping cached page: {page_url}", "existing", 2)
        #     return

        match = re.search(r"/pages/([^/?]+)", normalized_page_url)
        if not match:
            print_c(f"Skipping non-Canvas page: {normalized_page_url}", "error", 2)
            return

        page_slug = match.group(1)
        api_url = f"https://{self.domain}/api/v1/courses/{course_id}/pages/{page_slug}"

        try:
            response = requests.get(api_url, headers={"Authorization": f"Bearer {self.token}"}, timeout=30)
            
            if response.status_code != 200:
                print_c(f"Failed to fetch page: {page_url}", "error", 2)
                return

            page_data = response.json()
            page_title = page_data.get("title", "Untitled Page").strip().replace("/", "-")  # Sanitize filename
            page_html = page_data.get("body", "")

            if not page_html:
                print_c(f"Warning: Page {page_slug} has no content. Skipping.", "error", 2)
                return

            # Find all iframe sources and download them
            iframe_links = re.findall(r'<iframe[^>]+src="([^"]+)"', page_html)
            iframe_replacements = {}

            for iframe_src in iframe_links:
                clean_iframe_src_normalized = self._normalize_url(iframe_src)

                if clean_iframe_src_normalized in self.download_cache:
                    local_iframe_file = self.download_cache[clean_iframe_src_normalized]  # Use cached file name
                else:
                    # Generate a unique local filename for the iframe
                    iframe_file_name = f"iframe_{len(self.download_cache) + 1}.html"
                    local_iframe_file = os.path.join(files_folder, iframe_file_name)

                    # Download iframe content
                    iframe_response = requests.get(iframe_src, headers={"Authorization": f"Bearer {self.token}"}, allow_redirects=True, timeout=30)
                    if iframe_response.status_code == 200:
                        with open(sanitize_filename(local_iframe_file), "w", encoding="utf-8") as iframe_file:
                            iframe_file.write(iframe_response.text)

                        print_c(f"Downloaded iframe: {iframe_file_name}", "new", 2)
                        self.download_cache[iframe_src] = iframe_file_name  # Cache iframe file mapping
                    else:
                        print_c(f"Failed to fetch iframe: {iframe_src}", "error", 2)
                        continue  # Skip this iframe if download fails

                # Ensure iframe replacement is only performed if a valid file exists
                if local_iframe_file:
                    iframe_replacements[iframe_src] = os.path.basename(local_iframe_file)

            # Replace iframe `src` with local references
            for original_src, local_file in iframe_replacements.items():
                page_html = page_html.replace(f'src="{original_src}"', f'src="{local_file}"')

            # Save the updated Canvas page as an HTML file
            html_file_path = os.path.join(files_folder, f"{page_title}.html")

            with open(sanitize_filename(html_file_path), "w", encoding="utf-8") as html_file:
                html_file.write(f"<html><head><title>{page_title}</title></head><body>{page_html}</body></html>")

            print_c(f"Saved Canvas page: {html_file_path}", "new", 2)

            # Extract and download all file links from the page
            links = re.findall(r'href="([^"]+)"', page_html) + re.findall(r'src="([^"]+)"', page_html)

            for link in links:
                if self._is_canvas_url(link):
                    if "/files/" in link:
                        file_id = self._extract_canvas_file_id(link)
                        if file_id:
                            file_obj = self.get_file_from_id(course_id, file_id)
                            if not isinstance(file_obj, dict) or "url" not in file_obj:
                                print_c("Error in _download_canvas_page: invalid item, skipping", "error", 2)
                                continue  # Skip invalid objects
                            self._download_file(file_obj["url"], [course_name, "Files"], file_obj["display_name"])
                    elif "/pages/" in link:
                        if link not in self.download_cache:
                            self.download_cache[link] = True  # Mark this page as visited
                            self._download_canvas_page(course_id, link, course_name)  # Recursively follow linked pages
        except Exception as e:
            print_c(f"Error in download_canvas_page. Exception type: {type(e).__name__}", "error", 2)
            print(f"Exception message: {str(e)}", "error", 2)
            print("Traceback details:", "error", 2)
            traceback.print_exc()


    def _is_canvas_url(self, url):
        """Checks if the URL belongs to the same Canvas instance."""
        return self.domain in url


    def _extract_canvas_file_id(self, file_url):
        """Extracts the file ID from a Canvas file URL."""
        match = re.search(r"/files/(\d+)", file_url)
        return match.group(1) if match else None



    def _normalize_url(self, url):
        """Removes query parameters from Canvas file URLs."""
        return re.sub(r"\?.*", "", url)


    def _download_file(self, file_url, folder_path, name="", downloadBodyOnly=False):
        """Downloads a file while caching metadata to avoid repeated checks.

        - If downloadBodyOnly=True, saves only the HTML content (used for pages).
        - Otherwise, downloads the full binary file (used for PDFs, PPTXs, etc.).
        """

        # Normalize the URL for caching (removes verification tokens)
        normalized_file_url = self._normalize_url(file_url)

        # Ensure directory exists
        dir_path = sanitize_path(os.path.join(self.out_dir, *folder_path))
        os.makedirs(dir_path, exist_ok=True)

        # If file was already downloaded in this course, skip it
        if normalized_file_url in self.download_cache:
            print_c(f"Skipping {name or 'Unknown'} (Already downloaded & cached).", "existing", 2)
            return

        # Determine the file name
        file_name = name
        file_path = os.path.join(dir_path, file_name) if file_name else None

        # Ensure file url is valid
        if not file_url or not isinstance(file_url, str) or not file_url.startswith(("http://", "https://")):
            print_c(f"Error: URL of file is invalid (URL={file_url}). Skipping.", "error", 2)
            return

        # If name is not provided, try fetching it from headers
        try:
            if not file_name:
                response_head = requests.head(file_url, headers={"Authorization": f"Bearer {self.token}"}, allow_redirects=True)
                if response_head.status_code != 200:
                    print_c(f"Error: Could not fetch metadata for {file_url}. Skipping.", "error", 2)
                    return

                content_header = response_head.headers.get("Content-Disposition")
                file_name = get_file_name_by_header(content_header) if content_header else "unknown_file"
                file_path = os.path.join(dir_path, file_name)
            else:
                # Fetch file metadata (size) only if needed
                response_head = requests.head(file_url, headers={"Authorization": f"Bearer {self.token}"}, allow_redirects=True)
                if response_head.status_code != 200:
                    print_c(f"Error: Could not fetch metadata for {file_name}. Skipping.", "error", 2)
                    return

            file_size = int(response_head.headers.get("Content-Length", 0))

            # Check if file exists and compare sizes
            if os.path.exists(file_path):
                existing_size = os.path.getsize(file_path)

                if existing_size == file_size:
                    print_c(f"Skipping {file_name} (Already downloaded & sizes match).", "existing", 2)
                    self.download_cache[file_url] = True  # Mark as cached
                    return  # Skip downloading

                # If size mismatch, rename the new file
                base_name, ext = os.path.splitext(file_name)
                count = 1
                new_file_name = f"{base_name}_{count}{ext}"
                while os.path.exists(os.path.join(dir_path, new_file_name)):
                    count += 1
                    new_file_name = f"{base_name}_{count}{ext}"

                print_c(f"File size mismatch for {file_name}. Saving as {new_file_name}.", "error", 2)
                file_name = new_file_name  # Use the new name
                file_path = os.path.join(dir_path, file_name)

            # Start the actual file download (only if needed)
            download_response = requests.get(file_url, headers={"Authorization": f"Bearer {self.token}"}, allow_redirects=True, stream=not downloadBodyOnly, timeout=120)

            # Ensure content length for progress bar
            content_len = download_response.headers.get("Content-Length")
            total_len = int(content_len) if content_len else None

            # Handle HTML downloads (if downloadBodyOnly is True)
            if downloadBodyOnly:
                file_path = os.path.splitext(file_path)[0] + ".html"  # Ensure it saves as .html
                with open(sanitize_filename(file_path), "w", encoding="utf-8") as file:
                    file.write(download_response.text)

                print_c(f"Saved HTML content: {file_name}", "new", 2)
            else:
                # Download with progress bar for binary files
                print_c(" | ".join((f"{0:3.0f}%", file_name)), "new", 2, end="\r")

                with open(sanitize_filename(file_path), "wb") as file:
                    progress = 0

                    for data in download_response.iter_content(chunk_size=4096):
                        file.write(data)
                        progress += len(data)

                        if total_len:
                            perc = (progress / total_len) * 100
                            print_c(" | ".join((f"{perc:3.0f}%", file_name)), "new", 2, end="\r")

                print(end="\n")

            # Mark file as downloaded in this course
            self.download_cache[file_url] = True

            
        except requests.exceptions.RequestException as e:
            # network / HTTP errors (ConnectionError, Timeout, etc.)
            print_c(f"Network error downloading {name or file_url}: {e}", "error", 2)
            return False

        except Exception as e:
            # unexpected errors
            print_c(f"Unexpected error in file_download: {type(e).__name__}: {e}", "error", 2)
            traceback.print_exc()
            return False







if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Download files from Canvas")
    parser.add_argument("token", metavar="TOKEN", help="Canvas access token")
    parser.add_argument("domain", metavar="DOMAIN", help="Canvas domain")

    parser.add_argument(
        "-f",
        metavar="FROM",
        help="Download from modules, folders, pages or all (Default: all)",
        choices=("modules", "folders", "pages", "all"),
        default="all"
    )

    parser.add_argument(
        "-o",
        type=str,
        metavar="OUT",
        help="Out directory (Default: CanvasFiles)",
        default="CanvasFiles"
    )

    parser.add_argument(
        "--favorites", action="store_true", help="Get only favorite courses"
    )

    args = parser.parse_args()

    API = CanvasDownloader(args.domain, args.token, args.o)
    API.download_files(args.favorites, args.f)
