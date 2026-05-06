from dataclasses import dataclass
from typing import List
from google import genai
import json
from pathlib import Path
from datetime import datetime
from clean_up_recipes_txt import clean_up_text
from fpdf import FPDF

@dataclass
class Recipe:
    """
    Creates a Recipe, which has a title and a sequence of steps
    """
    title: str
    steps: List[str]
    cooking_time_minutes: int = None

    def combine_steps(self):
        return "\n".join(self.steps)

    def estimate_cooking_time(self) -> int:
        """ prompts Gemini to get an estimated prep and cooking time in minutes"""
        # todo: move to another file
        api_key = 'AIzaSyC7Is9A9WsLTGMShOdoUZCxiaAtzU5_tgg'
        client = genai.Client(api_key=api_key)
        

        prompt = f"Given the following recipe steps, estimate the total amount of \
            time in minutes, including all tasks, such as washing vegetables, \
        heating up the oven, etc. Duplicate all the amounts, the recipe is \
        written for 2 meals, but it should be 4.\
        Return only the time in minutes, if there is a range, take the highest \
        estimated number. Recipe: {self.combine_steps()}"

        print('Estimating cooking time with Gemini...')
        try:
            response = client.models.generate_content(
                model="gemini-2.5-flash-lite", contents=prompt,
                config={
                    "temperature": 0.5,
                    "response_mime_type": "application/json",
                    "response_json_schema": {
                        "type": "OBJECT",
                        "properties": {"minutes": {"type": "INTEGER"}},
                        "required": ["minutes"],
                    }
                }
                )
        except:
            print('Bad response from Gemini')
            return
        
        try:
            minutes = json.loads(response.text)['minutes']
            if isinstance(minutes, int):
                self.cooking_time_minutes = minutes
                return
        except Exception as e:
            print(f'Gemini failed response: {e}')


    def to_pdf(self):
        # grab day in yymmdd
        date = datetime.today().strftime('%Y%m%d')
        calendar_year = datetime.today().year
        week_number = datetime.today().isocalendar().week

        # create folder, if not already existing
        file_path_dir = Path(f'recipes/{calendar_year}_Week_{week_number + 1}')
        file_path_dir.mkdir(parents=True, exist_ok=True)
                
        print(f'Saving recipe {self.title}...')
        # skip saving if file already exists
        filepath = file_path_dir/f'{self.title}'
        if filepath.with_suffix('.pdf').exists():
            print('Recipe already saved')
            return

        # improve formatting of txt file
        txt_to_save = clean_up_text(self.combine_steps())
        # save as PDF
        pdf_report = PdfReport(self.title)
        pdf_report.generates_report(self.title, txt_to_save, filepath)


class PdfReport:
    """ from a str title and str steps and generates a PDF """

    def __init__(self,filename: str):
        self.filename = filename

    def generates_report(self,
                        title: str,
                        steps: str,
                        filepath: Path,
                        family='DejaVu'):
        # use a third-party library to generate the report
        pdf = FPDF()
        pdf.add_page()
        # path to font
        font_dir = Path(r'dejavu-fonts-ttf-2.37/ttf')
        pdf.add_font(family, style='B', fname=font_dir/"DejaVuSans-Bold.ttf", uni=True)
        pdf.add_font(family, style='', fname=font_dir/"DejaVuSans.ttf", uni=True)
        # add title
        pdf.set_font(family=family, size=16, style='B')
        pdf.multi_cell(w=0, h=10, txt=title, align='C', border=0)

        # add steps
        # break steps into a list for printing each step separately
        list_of_steps = steps.split('\n')
        pdf.set_font(family=family, size=12)
        for step in list_of_steps:
            pdf.multi_cell(w=0, h=8, txt=step, border=0)

        pdf.output(f'{filepath}.pdf')
