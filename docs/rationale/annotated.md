# Why typing.Annotated?

I chose to use [typing.Annotated][] to attach metadata to types instead of using custom classes to avoid cluttering the
object model. The Python community has been slowly moving away from arbitrary mix-in classes to a purer form of OOP.
Personally, I care less about OOP purity and more about using the right tool for the job. In this case, I am attaching
descriptive metadata to types so using `typing.Annotated` is the right tool for the job. If I were providing functionality
that is used inside of an application class, then I would use a mix-in class. The goal is to keep the **application's**
object model clean.
