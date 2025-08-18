import json
import os
import sys
from datetime import datetime
import colorama
import requests
import io
import random
import concurrent.futures as futures
import re
from PIL import Image
from selectolax.parser import HTMLParser


class EbayGrabber:
    def __init__(self, config):
        self.url = "https://picclick.de"
        self.config = config

    def getimage(self, imageurl, imagename):
        r = requests.get(imageurl)

        image_data = r.content
        image = Image.open(io.BytesIO(image_data))

        # CLEAN EXIF
        data = list(image.getdata())
        image_without_exif = Image.new(image.mode, image.size)
        image_without_exif.putdata(data)
        image_without_exif.convert("RGB").save(imagename)
        image_without_exif.close()

    def writeinserat(self, inserat, inseratpath):
        try:
            os.mkdir(inseratpath)
            datum = datetime.today().strftime('%d.%m.%Y')

            if self.config["Grabber"]["fwready"]:
                with open(inseratpath + "/fwimport.txt", "w") as insifile:
                    insifile.write(
                        f"{inserat['inseratlink']}\n{datum}\n{inserat['title']}\n{inserat['price']}\n{inserat['description']}")

            with open(inseratpath + "/inserat.json", "w") as insijson:
                json.dump(inserat, insijson, indent=2, ensure_ascii=True)

            for image_url in inserat["imageurls"]:
                path_pic = f"{inseratpath}/pic{inserat['imageurls'].index(image_url)}" + ".jpg"
                self.getimage(image_url, path_pic)

        except FileExistsError:
            return False
        return True

    def getinseratlink(self, iid):
        try:
            r = requests.get(f"{self.url}?q={iid}")
            if not r.ok:
                return None

            parser = HTMLParser(r.text)

            # Look for the specific item
            item = parser.css_first(f"#item-{iid}")
            if item:
                link_elem = item.css_first("a")
                if link_elem:
                    href = link_elem.attributes.get("href", "")
                    if href.startswith("/"):
                        return self.url + href
                    return href

            # Fallback: look for any item with this ID in the results
            all_items = parser.css(".items.list-unstyled li a")
            for link in all_items:
                href = link.attributes.get("href", "")
                if iid in href:
                    if href.startswith("/"):
                        return self.url + href
                    return href

            return None

        except Exception as e:
            print(f"Error getting inserat link for {iid}: {e}")
            return None

    def getdescription(self, inseraturl):
        try:
            r = requests.get(inseraturl)
            if not r.ok:
                return "Description not available"

            parser = HTMLParser(r.text)

            # Try multiple selectors for description
            desc_selectors = [
                ".summary.description span",
                ".description",
                "#description",
                ".item-description",
                "[data-testid='description']"
            ]

            for selector in desc_selectors:
                desc_elem = parser.css_first(selector)
                if desc_elem:
                    desc_text = desc_elem.text().strip()
                    if desc_text:
                        return desc_text

            # Fallback: look for any paragraph or div with substantial text
            text_elements = parser.css("p, div")
            for elem in text_elements:
                text = elem.text().strip()
                if len(text) > 50:  # Substantial text
                    return text

            return "Description not available"

        except Exception as e:
            print(f"Error getting description from {inseraturl}: {e}")
            return "Description not available"

    def convertimagelink(self, ebaylink):
        ebay_match = re.search(r'/([^/]+)/s-l225\.jpg$', ebaylink)
        if ebay_match:
            ebay_part = ebay_match.group(1)
            picclick_link = f"https://www.picclickimg.com/{ebay_part}/image.webp"
            return picclick_link
        else:
            return None

    def harvestads(self, keyword):
        page = 1
        max_pages = 5  # Limit to prevent infinite loops

        while page <= max_pages:
            # Use the regular search URL with pagination
            search_url = f"{self.url}?q={keyword}&page={page}"

            print(f"Searching page {page}: {search_url}")

            try:
                r = requests.get(search_url)

                if not r.ok:
                    print(f"Request failed with status {r.status_code}")
                    break

                parser = HTMLParser(r.text)

                # Get all items (excluding Amazon items)
                all_items = parser.css(".items.list-unstyled li")
                ebay_items = [item for item in all_items if "amazon" not in item.attributes.get("class", "")]

                if not ebay_items:
                    print(f"No eBay items found on page {page}")
                    break

                print(f"Found {len(ebay_items)} eBay items on page {page}")

                for item in ebay_items:
                    try:
                        # Extract item ID from the id attribute
                        item_id_attr = item.attributes.get("id", "")
                        if not item_id_attr.startswith("item-"):
                            continue

                        item_id = item_id_attr.replace("item-", "")

                        # Extract title from link
                        title_elem = item.css_first("a")
                        if not title_elem:
                            continue

                        title = title_elem.text().strip()
                        # Remove observer count from title
                        if "Beobachter" in title:
                            title = title.split("Beobachter", 1)[-1].strip()

                        # Extract price
                        price_elem = item.css_first(".price")
                        if not price_elem:
                            continue

                        price_text = price_elem.text().strip()
                        # Extract numeric price
                        price_match = re.search(r'EUR\s*([\d,]+(?:\.\d{2})?)', price_text)
                        if not price_match:
                            continue

                        price_str = price_match.group(1).replace(',', '.')
                        try:
                            price = float(price_str)
                        except ValueError:
                            continue

                        # Apply price filters
                        if price < self.config['Inserate']['minprice'] or price > self.config['Inserate']['maxprice']:
                            continue

                        # Extract main image
                        img_elem = item.css_first("img")
                        if not img_elem:
                            continue

                        main_image_url = img_elem.attributes.get("src", "")
                        if not main_image_url:
                            continue

                        # For now, just use the main image (additional images would need separate requests)
                        image_urls = [main_image_url]

                        # Apply minimum pictures filter
                        if len(image_urls) < self.config['Inserate']['minpictures']:
                            continue

                        # Apply price reduction
                        final_price = round(price * self.config["Inserate"]["pricereduction"])

                        inserat = {
                            "title": title,
                            "id": item_id,
                            "price": final_price,
                            "imageurls": image_urls
                        }

                        # Create directory path
                        safe_title = inserat['title'].replace('/', '|').replace('\\', '|')[:100]  # Limit length
                        inseratpath = f"inserate/[{inserat['price']}€] {safe_title} {inserat['id']}"

                        if not os.path.exists(inseratpath):
                            try:
                                # Get full item link and description
                                inserat["inseratlink"] = self.getinseratlink(inserat["id"])
                                if inserat["inseratlink"]:
                                    inserat["description"] = self.getdescription(inserat["inseratlink"])

                                    if self.writeinserat(inserat, inseratpath):
                                        print(f"[{colorama.Fore.GREEN}+{colorama.Fore.RESET}] {inserat['title']}")
                                    else:
                                        print(
                                            f"[{colorama.Fore.YELLOW}!{colorama.Fore.RESET}] Failed to write {inserat['title']}")
                                else:
                                    print(
                                        f"[{colorama.Fore.YELLOW}!{colorama.Fore.RESET}] Could not get link for {inserat['title']}")

                            except Exception as e:
                                print(f"[{colorama.Fore.RED}!{colorama.Fore.RESET}] Error processing {title}: {e}")

                    except Exception as e:
                        print(f"[{colorama.Fore.RED}!{colorama.Fore.RESET}] Error parsing item: {e}")
                        continue

                page += 1

            except Exception as e:
                print(f"Error fetching page {page}: {e}")
                break


def configloader() -> dict:
    with open("config.json", "r") as configfile:
        return json.load(configfile)


def kijijigrabber():
    os.system("clear") if sys.platform != "win32" else os.system("cls")
    print(f"""{colorama.Fore.RED} 
        ╦╔═╦ ╦╦ ╦╦  ╔═╗╦═╗╔═╗╔╗ ╔╗ ╔═╗╦═╗
        ╠╩╗║ ║║ ║║  ║ ╦╠╦╝╠═╣╠╩╗╠╩╗║╣ ╠╦╝   KijijiGrabber v1 by xqi
        ╩ ╩╩╚╝╩╚╝╩  ╚═╝╩╚═╩ ╩╚═╝╚═╝╚═╝╩╚═       
        """)
    input()


def willhabengrabber():
    os.system("clear") if sys.platform != "win32" else os.system("cls")
    print(f"""{colorama.Fore.BLUE} 
            ╦ ╦┬┬  ┬  ┬ ┬┌─┐┌┐ ┌─┐┌┐┌   
            ║║║││  │  ├─┤├─┤├┴┐├┤ │││  WillhabenGrabber v1 by xqi
            ╚╩╝┴┴─┘┴─┘┴ ┴┴ ┴└─┘└─┘┘└┘    
        """)

    config = configloader()
    input()


def ebaydegrabber():
    os.system("clear") if sys.platform != "win32" else os.system("cls")
    print(f"""{colorama.Fore.RED} 
         *         ▓█████  ▄▄▄▄ *  ▄▄▄     ▓██   ██▓             *
             *     ▓█   ▀ ▓█████▄ ▒████▄    ▒██  ██▒    *                 *
        *          ▒███   ▒██▒ ▄██▒██  ▀█▄   ▒██ ██░                            *
                   ▒▓█  ▄ ▒██░█▀  ░██▄▄▄▄██  ░ ▐██▓░     EbayGrabber v1     *
             *     ░▒████▒░▓█  ▀█▓ ▓█   ▓██▒ ░ ██▒▓░     by xqi            *
                   ░░ ▒░ ░░▒▓███▀▒ ▒▒   ▓▒█░  ██▒ ▒                     *
              *     ▒       ▒    ▒   ▒   ▒      ▒    *                        *
              *      ▒   *      ▒      ▒       ▒            *            *
    """)

    config = configloader()

    with open("keywords.txt", "r") as kwfile:
        keywords = kwfile.read().splitlines()

    grabber = EbayGrabber(config)

    with futures.ThreadPoolExecutor(max_workers=config["Grabber"]["maxthreads"]) as executor:
        for kw in keywords:
            if config["Grabber"]["randomkeywords"]:
                keyword = random.choice(keywords)
                keywords.remove(keyword)
            else:
                keyword = kw
            executor.submit(grabber.harvestads, keyword)


def main():
    while True:
        colorama.init(autoreset=True)
        os.system("clear") if sys.platform != "win32" else os.system("cls")

        print(f" {colorama.Fore.RED}Inserategrabber by xqi ")
        print(f"[{colorama.Fore.CYAN}0{colorama.Fore.RESET}] Exit Grabber")
        print(f"[{colorama.Fore.CYAN}1{colorama.Fore.RESET}] Kijiji.ca Grabber")
        print(f"[{colorama.Fore.CYAN}2{colorama.Fore.RESET}] Ebay.de Grabber")
        print(f"[{colorama.Fore.CYAN}3{colorama.Fore.RESET}] Willhaben.at Grabber")
        print("")
        
        try:
            command = input(f"${colorama.Fore.CYAN}MENU{colorama.Fore.RESET}$ : ")
        except (EOFError, KeyboardInterrupt):
            print(f"\n{colorama.Fore.YELLOW}Exiting...{colorama.Fore.RESET}")
            sys.exit(0)

        match command:
            case "0":
                sys.exit()
            case "1":
                kijijigrabber()
            case "2":
                ebaydegrabber()
            case "3":
                willhabengrabber()
            case _:
                print(f"{colorama.Fore.RED}Invalid option. Please choose 0-3.{colorama.Fore.RESET}")
                input("Press Enter to continue...")
                continue


if __name__ == "__main__":
    main()
