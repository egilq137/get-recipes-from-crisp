from pathlib import Path
import re
import argparse


def clean_up_text(text: str):
    # todo: this needs to combine all the list into a single string
    # insert new line before any .digit. pattern
    text = re.sub(r'\b(\d+)\.', r'\n\1.', text)

    # insert empty line between steps
    text = re.sub(r'\b(?!1)(\d+)\.', r'\n\1.', text)

    # remove double spaces
    text = re.sub("\n{3,}", "\n\n", text)
    
    return text
    
if __name__ == '__main__':
    # set a command-line argument to only clean up recipes of a given data, 
    # otherwise it would redo the steps for clean recipes
    # alternative: save them in a different folder, and only do so for those 
    # files that are not already present in that folder
    parser = argparse.ArgumentParser(description='filtering help')
    parser.add_argument('date', help='the starting string of the file ' \
    'indicating the date of the recipe')
    args = parser.parse_args()

    recipes_folder = Path('recipes')

    # iterate through all files in the folder
    for file in recipes_folder.glob('*'):
        try:
            text = file.read_text(encoding='utf-8')
        except UnicodeDecodeError:
            print(f"couldn't read file {file}")
            pass
        
        if file.name.startswith(args.date):
            # insert new line before any .digit. pattern
            text = re.sub(r'\b(\d+)\.', r'\n\1.', text)

            # insert empty line between steps
            text = re.sub(r'\b(?!1)(\d+)\.', r'\n\1.', text)

            # remove double spaces
            text = re.sub("\n{3,}", "\n\n", text)
            
            file.write_text(text, encoding='utf-8')