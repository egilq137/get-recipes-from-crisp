from typing import  List
import os

from models import Recipe
from email_handler import send_email
from recipe_scraper import scrape_current_recipes
from translator import translate

# create folder for translated recipes
os.makedirs('recipes', exist_ok=True)


def process_recipes(recipes: List[Recipe]) -> List[Recipe]:
    """ Takes a list of recipes and do the following:
    - translate each recipe
    - save each recipe as a pdf file
    - estimate cooking time for each recipe
    - email the recipes as attachments
    return: list of translated recipes
    """

    # save each recipe after translating
    translated_recipes: List[Recipe] = []
    for recipe in recipes:
        translated_recipe = translate(recipe)
        translated_recipe.to_pdf()
        translated_recipe.estimate_cooking_time()

        translated_recipes.append(translated_recipe)

    return translated_recipes


def main():

    list_of_recipes = scrape_current_recipes()
    translated_recipes = process_recipes(list_of_recipes)    
    send_email(translated_recipes)
    

if __name__ == "__main__":
    main()