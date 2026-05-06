from typing import List
from models import Recipe
from playwright.sync_api import sync_playwright


def extract_steps(steps_locator):
    steps_in_html = steps_locator.locator("h1.text")
        
    steps: List[str] = []
    num_steps = steps_in_html.count()
    for i in range(num_steps):
        # todo: names are confusing in this loop, not clear what is what
        step_i = steps_in_html.nth(i)
        step_number: str = step_i.text_content().strip()

        # go to the parent div containing the full text
        html_row = step_i.locator("xpath=../..")
        # text is in the second column
        step_text = html_row.locator("> div.view").nth(1) 
        # avoid duplication by selecting only 
        step_description: str = step_text.locator(":scope > span.text").all_text_contents()[0].strip()
        steps.append(f"{step_number}. " + step_description + "\n")

    return steps


def scrape_current_recipes() -> List[Recipe]:
    """ scrape recipes from Crisp.com
     return: List of Meals """
    
    with sync_playwright() as playwright:
        print('launching browser...')
        browser = playwright.firefox.launch()
        page = browser.new_page()
        page.goto('https://crisp.nl/weekbox')

        # accept cookies
        page.get_by_text('accepteren').click() 

        # click on vegan box
        page.locator("div.mealkitLanding20:has(span:text('vegan'))").click()

        # choose the 3 links for the recipes
        links = page.locator("div.mealkitLanding8.mealkitLanding10 a")
        assert links.count() == 3, "Number of links not matching"
        
        num_links: int = links.count()
        recipes: List[Recipe] = []
        for i in range(num_links):
            recipes_dict = {}
            page_recipe = browser.new_page()
            recipe_url = 'https://crisp.nl' + links.nth(i).get_attribute('href')
            page_recipe.goto(recipe_url)
            # accept cookies
            page_recipe.get_by_text('accepteren').click()

            # grab recipe title
            title = page_recipe.locator("span.mealkitRecipe12.mealkitRecipe13").last.inner_text().strip()
            
            # locate steps section
            steps_locator = page_recipe.locator("div.view.mealkitRecipe18.mealkitRecipe21")
            print('extracting recipe steps...')
            steps = extract_steps(steps_locator)
            recipes.append(Recipe(title, steps))
        browser.close()
    return recipes
