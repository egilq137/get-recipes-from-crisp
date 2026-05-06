import deepl

from models import Recipe

class DeepLTranslator:
    """ receives a meal in Dutch and translates it into English """

    # class attributes
    # todo: move to a different file
    api_key = "91d42cc3-9115-4033-bf03-4bd9887b4ec9:fx"
    deepl_client = deepl.DeepLClient(api_key) 

    def __init__(
            self,
            target_lang: str = 'EN-GB',
            translation_model: str = None):
        self.target_lang = target_lang
        self.translation_model = translation_model
   
    def translate_text(self, txt: str) -> str:
        """ translates a single string obect """
        return self.deepl_client.translate_text(
        txt, 
        target_lang=self.target_lang, 
        model_type=self.translation_model).text
    
    def translate_recipe(self, recipe: Recipe) -> Recipe:
        """ returns a translated Meal object """
        # translate the title
        title_translated = self.translate_text(recipe.title)
        # translate the steps
        # combine into one str for translation. use # to separate the steps later
        steps_combined = "#".join(recipe.steps)
        steps_translated = self.translate_text(steps_combined)
        steps_translated = steps_translated.split('#')

        # combine into a new Meal
        translated_recipe = Recipe(title_translated, steps_translated)
        return translated_recipe
    

def translate(recipe: Recipe) -> Recipe:
    # start the Translator obejct
        translator = DeepLTranslator(translation_model="prefer_quality_optimized")
        return translator.translate_recipe(recipe)