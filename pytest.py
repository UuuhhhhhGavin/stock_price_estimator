import os

test_var="hello, this is python"
print(test_var)

project_dir = os.getcwd()
file_path=os.path.join(project_dir, 'test_output.txt'
                       )
with open(file_path, 'w', encoding='utf-8') as file:
    file.write(test_var)
print(f"File '{file_path}' saved successfully.")
